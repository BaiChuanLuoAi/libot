#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整合的图像和视频生成API服务
支持：
- 图像生成（直接调用ComfyUI API）
- 视频生成（文生视频竖屏和图生视频竖屏，各5并发，超时10分钟）
"""

import os
import json
import time
import base64
import uuid
import random
import requests
import threading
import hmac
import hashlib
import hmac
import sys
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# Import bot database
sys.path.append(os.path.join(os.path.dirname(__file__), 'tg_bot'))
try:
    from database import Database
    # 数据库在 tg_bot/data 目录下
    bot_db = Database(os.path.join(os.path.dirname(__file__), 'tg_bot', 'data', 'bot_users.db'))
    print("✅ Bot database initialized successfully")
except ImportError as e:
    bot_db = None
    print(f"⚠️  Bot database not available: {e}")
except Exception as e:
    bot_db = None
    print(f"⚠️  Bot database initialization failed: {e}")

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": True
    }
})

# 添加CORS响应头处理
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.headers.add('Access-Control-Expose-Headers', 'Content-Type')
    return response

# ===== 配置 =====
# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()  # 加载 .env 文件中的环境变量

SERVER_AUTH_KEY = os.getenv('SERVER_AUTH_KEY', 'default-insecure-key')  # 从环境变量读取

# Plisio配置
PLISIO_SECRET_KEY = os.getenv('PLISIO_SECRET_KEY', '')

# Telegram Bot Token (用于发送通知)
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

# Admin IDs for notifications
ADMIN_IDS_STR = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_STR.split(',') if id.strip()] if ADMIN_IDS_STR else []

# ComfyUI 直接API配置（图像生成）
COMFYUI_API_URL = os.getenv('COMFYUI_API_URL', "http://dx.qyxc.vip:18188")  # ComfyUI服务器地址
COMFYUI_CLIENT_ID = str(uuid.uuid4())

# ComfyUI 视频生成配置 - 直连端点（不再使用RunPod）
COMFYUI_VIDEO_API_URL = os.getenv('COMFYUI_VIDEO_API_URL', "https://n008.unicorn.org.cn:20155")  # 视频生成专用ComfyUI端点
COMFYUI_VIDEO_CLIENT_ID = str(uuid.uuid4())

# 目录配置
FILES_DIR = os.path.join(os.getcwd(), "files")
IMAGES_DIR = os.path.join(FILES_DIR, "images")
LOGS_DIR = os.path.join(os.getcwd(), "logs")

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# 并发控制 - 每种类型各5个并发
MAX_CONCURRENT_T2V = 5  # 文生视频竖屏
MAX_CONCURRENT_I2V = 5  # 图生视频竖屏
t2v_semaphore = threading.Semaphore(MAX_CONCURRENT_T2V)
i2v_semaphore = threading.Semaphore(MAX_CONCURRENT_I2V)
t2v_count = 0
i2v_count = 0
count_lock = threading.Lock()

# 视频超时时间：10分钟
VIDEO_TIMEOUT = 600

# 文件清理配置：基于存储空间大小
MAX_STORAGE_SIZE_GB = 10  # 最大存储空间10GB
CLEANUP_SIZE_GB = 2  # 超过限制时删除2GB内容
CLEANUP_CHECK_INTERVAL = 600  # 每10分钟检查一次是否到清理时间
CLEANUP_HOUR = 3  # 上海时间3点执行清理（UTC+8）

# 统计数据
stats_lock = threading.Lock()
daily_stats = {
    "image": {"total": 0, "success": 0, "failed": 0},
    "video_t2v": {"total": 0, "success": 0, "failed": 0},
    "video_i2v": {"total": 0, "success": 0, "failed": 0},
}

# ===== 工作流模板 =====
# 图像生成工作流（带LoRA）
IMAGE_WORKFLOW = {
    "3": {
        "inputs": {
            "seed": 0,
            "steps": 9,
            "cfg": 1,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1,
            "model": ["19", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["13", 0]
        },
        "class_type": "KSampler"
    },
    "6": {
        "inputs": {
            "text": "",
            "clip": ["19", 1]
        },
        "class_type": "CLIPTextEncode"
    },
    "7": {
        "inputs": {
            "text": "blurry, ugly, bad quality, distorted",
            "clip": ["19", 1]
        },
        "class_type": "CLIPTextEncode"
    },
    "8": {
        "inputs": {
            "samples": ["3", 0],
            "vae": ["17", 0]
        },
        "class_type": "VAEDecode"
    },
    "9": {
        "inputs": {
            "filename_prefix": "ComfyUI",
            "images": ["8", 0]
        },
        "class_type": "SaveImage"
    },
    "13": {
        "inputs": {
            "width": 1024,
            "height": 1024,
            "batch_size": 1
        },
        "class_type": "EmptySD3LatentImage"
    },
    "16": {
        "inputs": {
            "unet_name": "z_image_turbo_fp8_e4m3fn.safetensors",
            "weight_dtype": "fp8_e4m3fn_fast"
        },
        "class_type": "UNETLoader"
    },
    "17": {
        "inputs": {
            "vae_name": "ae.safetensors"
        },
        "class_type": "VAELoader"
    },
    "18": {
        "inputs": {
            "clip_name": "qwen_3_4b.safetensors",
            "type": "lumina2",
            "device": "default"
        },
        "class_type": "CLIPLoader"
    },
    "19": {
        "inputs": {
            "lora_name": "pussy_000009750.safetensors",
            "strength_model": 0.6,
            "strength_clip": 0,
            "model": ["16", 0],
            "clip": ["18", 0]
        },
        "class_type": "LoraLoader"
    }
}

# 视频生成工作流（从文件加载）- 使用新的Cephalon工作流
def load_video_workflows():
    t2v_path = "video_wan2_2_14B_t2v_API_Cephalon.json"
    i2v_path = "video_wan2_2_14B_i2v_API_Cephalon.json"
    
    with open(t2v_path, "r", encoding="utf-8") as f:
        t2v_workflow = json.load(f)
    
    with open(i2v_path, "r", encoding="utf-8") as f:
        i2v_workflow = json.load(f)
    
    return t2v_workflow, i2v_workflow

T2V_WORKFLOW, I2V_WORKFLOW = load_video_workflows()

# ===== 文件清理函数 =====
def get_directory_size(directory):
    """计算目录总大小（字节）"""
    total_size = 0
    try:
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                total_size += os.path.getsize(filepath)
    except Exception as e:
        print(f"计算目录大小时出错: {e}")
    return total_size

def cleanup_old_files():
    """基于存储空间的智能清理：超过10GB时删除最旧的2GB文件"""
    try:
        # 计算当前目录总大小
        total_size = get_directory_size(IMAGES_DIR)
        total_size_gb = total_size / (1024 ** 3)
        
        print(f"📊 当前存储使用: {total_size_gb:.2f}GB / {MAX_STORAGE_SIZE_GB}GB")
        
        # 如果未超过限制，不进行清理
        if total_size_gb <= MAX_STORAGE_SIZE_GB:
            print(f"✅ 存储空间充足，无需清理")
            return
        
        print(f"⚠️  存储空间已达 {total_size_gb:.2f}GB，开始清理 {CLEANUP_SIZE_GB}GB 的旧文件...")
        
        # 获取所有文件及其修改时间
        files_info = []
        for filename in os.listdir(IMAGES_DIR):
            filepath = os.path.join(IMAGES_DIR, filename)
            if os.path.isfile(filepath):
                file_mtime = os.path.getmtime(filepath)
                file_size = os.path.getsize(filepath)
                files_info.append({
                    'path': filepath,
                    'name': filename,
                    'mtime': file_mtime,
                    'size': file_size
                })
        
        # 按修改时间排序（最旧的在前）
        files_info.sort(key=lambda x: x['mtime'])
        
        # 计算需要删除的大小（2GB）
        cleanup_bytes = CLEANUP_SIZE_GB * (1024 ** 3)
        
        cleaned_count = 0
        cleaned_size = 0
        
        # 从最旧的文件开始删除，直到删除了2GB
        for file_info in files_info:
            if cleaned_size >= cleanup_bytes:
                break
            
            try:
                os.remove(file_info['path'])
                cleaned_count += 1
                cleaned_size += file_info['size']
                print(f"🗑️  清理文件: {file_info['name']} ({file_info['size'] / 1024 / 1024:.2f}MB)")
            except Exception as e:
                print(f"删除文件失败 {file_info['name']}: {e}")
        
        final_size = total_size - cleaned_size
        final_size_gb = final_size / (1024 ** 3)
        print(f"✅ 清理完成: 删除 {cleaned_count} 个文件，释放 {cleaned_size / 1024 / 1024:.2f}MB 空间")
        print(f"📊 清理后存储: {final_size_gb:.2f}GB / {MAX_STORAGE_SIZE_GB}GB")
        
    except Exception as e:
        print(f"清理文件时出错: {e}")
        import traceback
        traceback.print_exc()

# 记录上次清理日期
last_cleanup_date = None

def should_run_cleanup():
    """检查是否应该运行清理（每天上海时间3点）"""
    global last_cleanup_date
    
    from datetime import datetime, timezone, timedelta
    
    # 上海时区 UTC+8
    shanghai_tz = timezone(timedelta(hours=8))
    now_shanghai = datetime.now(shanghai_tz)
    
    current_date = now_shanghai.date()
    current_hour = now_shanghai.hour
    
    # 如果今天已经清理过，则不再清理
    if last_cleanup_date == current_date:
        return False
    
    # 如果当前时间是3点（3:00-3:59），执行清理
    if current_hour == CLEANUP_HOUR:
        last_cleanup_date = current_date
        return True
    
    return False

def auto_cleanup_loop():
    """后台定时清理线程 - 每天上海时间3点执行一次"""
    print(f"🗑️  自动清理已启动：每天上海时间 {CLEANUP_HOUR}:00 检查存储空间")
    
    while True:
        try:
            time.sleep(CLEANUP_CHECK_INTERVAL)
            
            if should_run_cleanup():
                from datetime import datetime, timezone, timedelta
                shanghai_tz = timezone(timedelta(hours=8))
                now_shanghai = datetime.now(shanghai_tz)
                print(f"\n⏰ 定时清理触发 - 上海时间: {now_shanghai.strftime('%Y-%m-%d %H:%M:%S')}")
                cleanup_old_files()
        except Exception as e:
            print(f"自动清理循环错误: {e}")
            import traceback
            traceback.print_exc()

# 启动清理线程
cleanup_thread = threading.Thread(target=auto_cleanup_loop, daemon=True)
cleanup_thread.start()

# ===== 日志函数 =====
def log_request(service_type, status, details=None):
    """简化的统一日志记录"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(LOGS_DIR, f"requests_{today}.jsonl")
        
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "service": service_type,  # image, video_t2v, video_i2v
            "status": status,  # success, failed, rejected
            "details": details or {}
        }
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        
        # 更新内存统计
        with stats_lock:
            if service_type in daily_stats:
                daily_stats[service_type]["total"] += 1
                if status == "success":
                    daily_stats[service_type]["success"] += 1
                elif status == "failed":
                    daily_stats[service_type]["failed"] += 1
    except Exception as e:
        print(f"日志记录错误: {e}")

def get_daily_stats_from_logs():
    """从日志文件读取今天的统计数据"""
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(LOGS_DIR, f"requests_{today}.jsonl")
    
    stats = {
        "date": today,
        "image": {"total": 0, "success": 0, "failed": 0, "rejected": 0},
        "video_t2v": {"total": 0, "success": 0, "failed": 0, "rejected": 0},
        "video_i2v": {"total": 0, "success": 0, "failed": 0, "rejected": 0},
    }
    
    if not os.path.exists(log_file):
        return stats
    
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        service = entry.get("service")
                        status = entry.get("status")
                        
                        if service in stats:
                            stats[service]["total"] += 1
                            if status in ["success", "failed", "rejected"]:
                                stats[service][status] += 1
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"读取日志统计错误: {e}")
    
    return stats

def get_all_dates_stats():
    """获取所有日期的统计数据"""
    all_stats = []
    
    try:
        # 获取所有日志文件
        log_files = [f for f in os.listdir(LOGS_DIR) if f.startswith("requests_") and f.endswith(".jsonl")]
        log_files.sort(reverse=True)  # 最新的在前
        
        for log_file in log_files[:30]:  # 最多显示最近30天
            date_str = log_file.replace("requests_", "").replace(".jsonl", "")
            
            stats = {
                "date": date_str,
                "image": {"total": 0, "success": 0, "failed": 0, "rejected": 0},
                "video_t2v": {"total": 0, "success": 0, "failed": 0, "rejected": 0},
                "video_i2v": {"total": 0, "success": 0, "failed": 0, "rejected": 0},
            }
            
            log_path = os.path.join(LOGS_DIR, log_file)
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            service = entry.get("service")
                            status = entry.get("status")
                            
                            if service in stats:
                                stats[service]["total"] += 1
                                if status in ["success", "failed", "rejected"]:
                                    stats[service][status] += 1
                        except json.JSONDecodeError:
                            continue
            
            all_stats.append(stats)
    except Exception as e:
        print(f"读取历史统计错误: {e}")
    
    return all_stats

# ===== ComfyUI 直接API调用 =====
def submit_to_comfyui(workflow):
    """直接提交到ComfyUI - 参考test_comfyui_api.py的实现"""
    try:
        # 注意：payload的key是"prompt"，不是"workflow"
        prompt_data = {
            "prompt": workflow,  # ComfyUI API要求key为"prompt"
            "client_id": COMFYUI_CLIENT_ID
        }
        
        url = f"{COMFYUI_API_URL}/prompt"
        
        response = requests.post(
            url,
            json=prompt_data,
            timeout=120
        )
        
        if response.status_code != 200:
            print(f"❌ ComfyUI error {response.status_code}: {response.text[:100]}")
        
        response.raise_for_status()
        result = response.json()
        prompt_id = result.get("prompt_id")
        return prompt_id
    except requests.exceptions.ConnectionError as e:
        print(f"❌ ComfyUI连接错误: 无法连接到 {COMFYUI_API_URL}")
        print(f"   请检查: 1) ComfyUI是否运行在该地址 2) 网络是否可达")
        return None
    except requests.exceptions.Timeout:
        print(f"❌ ComfyUI超时: 连接超时（30秒）")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"❌ ComfyUI HTTP错误: {e}")
        print(f"   状态码: {e.response.status_code}")
        print(f"   响应: {e.response.text[:500]}")
        return None
    except Exception as e:
        print(f"❌ ComfyUI提交失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_comfyui_history(prompt_id):
    """获取ComfyUI执行历史"""
    try:
        response = requests.get(
            f"{COMFYUI_API_URL}/history/{prompt_id}",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"获取历史失败: {e}")
        return None

def get_comfyui_image(filename, subfolder="", folder_type="output"):
    """从ComfyUI获取生成的图片"""
    try:
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        response = requests.get(
            f"{COMFYUI_API_URL}/view",
            params=params,
            timeout=120  # 2分钟超时
        )
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"获取图片失败: {e}")
        return None

# ===== RunPod API调用（视频）=====
# ===== ComfyUI 视频生成API调用（直连）=====
def upload_image_to_comfyui(image_data_bytes, filename):
    """上传图片到ComfyUI服务器"""
    try:
        url = f"{COMFYUI_VIDEO_API_URL}/upload/image"
        
        # 构建multipart form data
        files = {
            'image': (filename, image_data_bytes, 'image/png')
        }
        
        response = requests.post(url, files=files, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        uploaded_name = result.get('name', filename)
        
        print(f"  → 图片已上传到ComfyUI: {uploaded_name}")
        return uploaded_name
    
    except Exception as e:
        print(f"上传图片到ComfyUI失败: {e}")
        raise

def submit_video_to_comfyui(workflow):
    """提交视频生成任务到ComfyUI（直连）"""
    try:
        prompt_data = {
            "prompt": workflow,
            "client_id": COMFYUI_VIDEO_CLIENT_ID
        }
        
        url = f"{COMFYUI_VIDEO_API_URL}/prompt"
        print(f"  → 连接到ComfyUI视频端点: {url}")
        print(f"  → Client ID: {COMFYUI_VIDEO_CLIENT_ID}")
        
        response = requests.post(
            url,
            json=prompt_data,
            timeout=120
        )
        print(f"  → HTTP状态: {response.status_code}")
        
        if response.status_code != 200:
            print(f"  → 响应内容: {response.text[:200]}")
        
        response.raise_for_status()
        result = response.json()
        
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise Exception("ComfyUI未返回prompt_id")
        
        print(f"✅ 任务已提交到ComfyUI，prompt_id: {prompt_id}")
        return {"prompt_id": prompt_id}
    
    except requests.exceptions.ConnectionError as e:
        print(f"❌ ComfyUI连接错误: 无法连接到ComfyUI视频API")
        raise Exception(f"无法连接到ComfyUI视频API，请检查网络")
    except requests.exceptions.Timeout:
        print(f"❌ ComfyUI超时（120秒）")
        raise Exception(f"ComfyUI视频API超时")
    except requests.exceptions.HTTPError as e:
        error_detail = e.response.text[:200] if e.response else str(e)
        print(f"❌ ComfyUI HTTP错误 {e.response.status_code}: {error_detail}")
        raise Exception(f"ComfyUI视频API错误 ({e.response.status_code}): {error_detail}")
    except Exception as e:
        print(f"❌ 提交失败: {e}")
        raise

def check_comfyui_video_status(prompt_id):
    """检查ComfyUI视频生成状态"""
    try:
        url = f"{COMFYUI_VIDEO_API_URL}/history/{prompt_id}"
        response = requests.get(url, timeout=30)
        
        if response.status_code != 200:
            print(f"⚠️ ComfyUI history API 返回状态码: {response.status_code}")
            return None
        
        history = response.json()
        
        if prompt_id not in history:
            return {"status": "IN_QUEUE"}
        
        task_info = history[prompt_id]
        
        # 调试：打印任务信息的关键字段
        print(f"📋 任务 {prompt_id} 信息:")
        print(f"  → 包含的键: {list(task_info.keys())}")
        if "status" in task_info:
            print(f"  → status: {task_info['status']}")
        if "outputs" in task_info:
            print(f"  → outputs keys: {list(task_info['outputs'].keys())}")
        
        # 检查是否完成
        if "outputs" in task_info and task_info["outputs"]:
            print(f"✅ 任务完成，返回 outputs")
            return {
                "status": "COMPLETED",
                "outputs": task_info["outputs"]
            }
        
        # 检查是否正在运行
        status_data = task_info.get("status", {})
        if status_data.get("status_str") == "success":
            print(f"✅ 任务成功（status_str），返回 outputs")
            return {
                "status": "COMPLETED",
                "outputs": task_info.get("outputs", {})
            }
        elif status_data.get("completed", False):
            print(f"✅ 任务完成（completed），返回 outputs")
            return {
                "status": "COMPLETED",
                "outputs": task_info.get("outputs", {})
            }
        
        # 检查是否有错误
        if "error" in task_info or status_data.get("status_str") == "error":
            print(f"❌ 任务失败")
            return {"status": "FAILED"}
        
        # 否则仍在处理中
        print(f"⏳ 任务处理中...")
        return {"status": "IN_PROGRESS"}
    
    except Exception as e:
        print(f"检查ComfyUI状态时出错: {e}")
        import traceback
        traceback.print_exc()
        return None

def download_comfyui_video(outputs):
    """从ComfyUI下载生成的视频 - 与图片提取方式一致"""
    try:
        # 调试：打印完整的outputs结构
        print(f"📦 ComfyUI返回的outputs结构:")
        import json
        print(json.dumps(outputs, indent=2, ensure_ascii=False))
        
        # 查找视频输出节点（SaveVideo可能使用 images、videos 或 gifs 字段）
        for node_id, node_output in outputs.items():
            print(f"  → 节点 {node_id}: {list(node_output.keys())}")
            
            # 按优先级尝试多种可能的输出格式
            # 1. 尝试 videos 字段
            if "videos" in node_output:
                videos = node_output["videos"]
                print(f"  → 找到videos字段，内容: {videos}")
                if videos and len(videos) > 0:
                    video_info = videos[0]
                    filename = video_info.get("filename")
                    subfolder = video_info.get("subfolder", "")
                    
                    if filename:
                        print(f"  → 提取视频文件: {filename}, 子目录: {subfolder}")
                        video_data = get_comfyui_video(filename, subfolder)
                        if video_data:
                            return video_data
            
            # 2. 尝试 images 字段（SaveVideo 节点可能使用这个）
            if "images" in node_output:
                images = node_output["images"]
                print(f"  → 找到images字段，内容: {images}")
                if images and len(images) > 0:
                    # 检查是否是视频文件（通过文件扩展名或 animated 标志）
                    image_info = images[0]
                    filename = image_info.get("filename", "")
                    subfolder = image_info.get("subfolder", "")
                    is_animated = node_output.get("animated", [False])[0] if "animated" in node_output else False
                    
                    # 如果文件名是视频格式或标记为动画
                    if filename and (filename.endswith(('.mp4', '.webm', '.avi', '.mov', '.gif')) or is_animated):
                        print(f"  → 提取视频文件: {filename}, 子目录: {subfolder}, 动画: {is_animated}")
                        video_data = get_comfyui_video(filename, subfolder)
                        if video_data:
                            return video_data
            
            # 3. 尝试 gifs 字段（某些节点可能输出gif）
            if "gifs" in node_output:
                gifs = node_output["gifs"]
                print(f"  → 找到gifs字段，内容: {gifs}")
                if gifs and len(gifs) > 0:
                    gif_info = gifs[0]
                    filename = gif_info.get("filename")
                    subfolder = gif_info.get("subfolder", "")
                    
                    if filename:
                        print(f"  → 提取GIF文件: {filename}, 子目录: {subfolder}")
                        video_data = get_comfyui_video(filename, subfolder)
                        if video_data:
                            return video_data
        
        print("❌ 未找到视频输出（检查了videos、images、gifs字段）")
        return None
    
    except Exception as e:
        print(f"下载ComfyUI视频时出错: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_comfyui_video(filename, subfolder=""):
    """从ComfyUI下载视频文件 - 与get_comfyui_image类似"""
    try:
        params = {
            "filename": filename,
            "type": "output"
        }
        if subfolder:
            params["subfolder"] = subfolder
        
        from urllib.parse import urlencode
        query_string = urlencode(params)
        url = f"{COMFYUI_VIDEO_API_URL}/view?{query_string}"
        
        print(f"  → 下载视频: {url}")
        
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        
        return response.content
    
    except Exception as e:
        print(f"下载视频失败: {e}")
        return None

# ===== API路由 =====
@app.route('/files/images/<path:filename>')
def serve_image(filename):
    """提供图片文件访问"""
    return send_from_directory(IMAGES_DIR, filename)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """获取统计信息 - 从日志文件读取真实数据"""
    # 从日志文件读取今天的统计
    today_stats = get_daily_stats_from_logs()
    
    # 添加当前并发信息
    with count_lock:
        today_stats["current_video_t2v"] = MAX_CONCURRENT_T2V - t2v_semaphore._value
        today_stats["current_video_i2v"] = MAX_CONCURRENT_I2V - i2v_semaphore._value
        today_stats["max_concurrent_t2v"] = MAX_CONCURRENT_T2V
        today_stats["max_concurrent_i2v"] = MAX_CONCURRENT_I2V
    
    return jsonify(today_stats)

@app.route('/api/stats/history', methods=['GET'])
def get_stats_history():
    """获取历史统计数据"""
    all_stats = get_all_dates_stats()
    return jsonify({
        "stats": all_stats,
        "total_days": len(all_stats)
    })

@app.route('/api/update_endpoint', methods=['POST'])
def update_endpoint():
    """更新ComfyUI端点（管理员功能）"""
    # 验证API Key
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header.replace("Bearer ", "") != SERVER_AUTH_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.json
        endpoint_type = data.get('type')  # 'image' or 'video'
        new_url = data.get('url')
        
        if not endpoint_type or not new_url:
            return jsonify({"error": "Missing type or url"}), 400
        
        if endpoint_type not in ['image', 'video']:
            return jsonify({"error": "Invalid type. Must be 'image' or 'video'"}), 400
        
        # 更新全局变量
        global COMFYUI_API_URL, COMFYUI_VIDEO_API_URL
        
        if endpoint_type == 'image':
            COMFYUI_API_URL = new_url.rstrip('/')
            print(f"✅ 图像ComfyUI端点已更新为: {COMFYUI_API_URL}")
        elif endpoint_type == 'video':
            COMFYUI_VIDEO_API_URL = new_url.rstrip('/')
            print(f"✅ 视频ComfyUI端点已更新为: {COMFYUI_VIDEO_API_URL}")
        
        return jsonify({
            "success": True,
            "type": endpoint_type,
            "new_url": new_url.rstrip('/')
        })
    
    except Exception as e:
        print(f"更新端点错误: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_endpoints', methods=['GET'])
def get_endpoints():
    """获取当前ComfyUI端点（管理员功能）"""
    # 验证API Key
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header.replace("Bearer ", "") != SERVER_AUTH_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "image_url": COMFYUI_API_URL,
        "video_url": COMFYUI_VIDEO_API_URL
    })

@app.route('/api/storage_status', methods=['GET'])
def get_storage_status():
    """获取存储使用情况（管理员功能）"""
    # 验证API Key
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header.replace("Bearer ", "") != SERVER_AUTH_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        # 计算当前存储使用
        total_size = get_directory_size(IMAGES_DIR)
        total_size_gb = total_size / (1024 ** 3)
        
        # 统计文件数量
        file_count = len([f for f in os.listdir(IMAGES_DIR) if os.path.isfile(os.path.join(IMAGES_DIR, f))])
        
        # 计算使用百分比
        usage_percent = (total_size_gb / MAX_STORAGE_SIZE_GB) * 100
        
        return jsonify({
            "used_gb": round(total_size_gb, 2),
            "max_gb": MAX_STORAGE_SIZE_GB,
            "usage_percent": round(usage_percent, 1),
            "file_count": file_count,
            "directory": IMAGES_DIR
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    """返回前端页面"""
    return send_from_directory('static', 'index.html')

# ===== 支付 Webhook =====
def send_telegram_notification(user_id: int, message: str):
    """发送Telegram通知给用户"""
    if not TELEGRAM_BOT_TOKEN:
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": user_id,
            "text": message,
            "parse_mode": "Markdown"
        }, timeout=5)
    except Exception as e:
        print(f"Failed to send TG notification: {e}")


def notify_admin(message: str):
    """发送通知给所有管理员"""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_IDS:
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    for admin_id in ADMIN_IDS:
        try:
            requests.post(url, json={
                "chat_id": admin_id,
                "text": message,
                "parse_mode": "Markdown"
            }, timeout=5)
        except Exception as e:
            print(f"Failed to send admin notification to {admin_id}: {e}")


@app.route('/webhooks/plisio', methods=['POST', 'GET'])
def webhook_plisio():
    """处理 Plisio 支付回调"""
    if not bot_db:
        return jsonify({"error": "Database not available"}), 503
    
    try:
        # Plisio 使用 GET 或 POST 方法发送回调
        # GET 方式通常用于 Status URL
        if request.method == 'GET':
            payload = request.args.to_dict()
        else:
            # POST 方式
            payload = request.json if request.is_json else request.form.to_dict()
        
        # 📝 生产日志：仅记录关键信息
        print(f"📥 Plisio webhook: {request.method}")
        
        # 解析 Plisio 回调数据
        order_number = payload.get('order_number')
        txn_id = payload.get('txn_id') or payload.get('id')  # Plisio 的交易 ID
        status = payload.get('status')  # Plisio 状态: 'pending', 'completed', 'error', 'cancelled'
        
        # 金额信息
        source_amount = payload.get('source_amount')  # 预期金额（USD）
        invoice_sum = payload.get('invoice_sum')  # 发票金额（加密货币）
        amount = payload.get('amount')  # 实际到账金额（加密货币）
        pending_amount = payload.get('pending_amount', '0')  # 待确认金额
        currency = payload.get('source_currency', 'USD')
        crypto_currency = payload.get('currency') or payload.get('psys_cid', 'BTC')
        
        # 检查是否超额支付
        expected_amount = float(invoice_sum) if invoice_sum else 0
        actual_amount = float(amount) if amount else 0
        is_overpaid = actual_amount > expected_amount if expected_amount > 0 else False
        overpaid_percentage = ((actual_amount / expected_amount) * 100) if expected_amount > 0 else 100
        
        # 优先使用 txn_id，如果没有则使用 order_number
        external_ref = txn_id or order_number
        
        if not external_ref:
            print(f"❌ Missing transaction reference")
            return jsonify({"error": "Missing transaction reference"}), 400
        
        # 从 order_number 中提取 user_id 和 package_key（格式：user_{user_id}_{package_key}_{timestamp}）
        if not order_number:
            print(f"⚠️  Missing order_number, cannot extract user info")
            return jsonify({"error": "Missing order_number"}), 400
            
        try:
            parts = order_number.split('_')
            user_id = int(parts[1]) if len(parts) > 1 else None
            package_key = parts[2] if len(parts) > 2 else 'pro'  # 默认 pro 套餐
        except:
            user_id = None
            package_key = 'pro'
        
        if not user_id:
            print(f"❌ Invalid order format: {order_number}")
            return jsonify({"error": "Invalid order_number format"}), 400
        
        # 套餐配置（与 bot.py 中的 PACKAGES 保持一致）
        PACKAGES = {
            'test': {'credits': 10, 'price': 1.00, 'name': '🧪 Test Pack'},
            'mini': {'credits': 60, 'price': 4.99, 'name': '🎓 Student Pack'},
            'pro': {'credits': 130, 'price': 9.99, 'name': '🔥 Pro Pack'},
            'ultra': {'credits': 450, 'price': 29.99, 'name': '👑 Whale Pack'}
        }
        
        # 获取套餐信息
        package = PACKAGES.get(package_key, PACKAGES['pro'])
        credits = package['credits']
        
        # 根据状态处理
        if status == 'pending':
            # 支付待确认
            return jsonify({"status": "ok"}), 200
            
        elif status == 'completed':
            # 支付成功
            
            # 检查是否已处理
            if (txn_id and bot_db.check_payment_exists(txn_id)) or \
               (order_number and bot_db.check_payment_exists(order_number)):
                return jsonify({"status": "already_processed"}), 200
            
            # 使用实际支付的 USD 金额（如果有的话）
            usd_amount = float(source_amount) if source_amount else package['price']
            
            # 添加积分
            success = bot_db.add_credits(
                user_id=user_id,
                amount=credits,
                money_amount=usd_amount,
                currency=currency,
                provider='plisio',
                external_ref=external_ref,  # 使用 txn_id 或 order_number
                description=f"Plisio crypto payment: {package['name']}"
            )
            
            if success:
                # 简洁日志
                overpaid_log = f" (overpaid {overpaid_percentage:.0f}%)" if is_overpaid else ""
                print(f"✅ Payment: User {user_id}, +{credits} credits, ${usd_amount}{overpaid_log}")
                
                # 构建消息（包含 overpaid 提示）
                overpaid_msg = ""
                if is_overpaid:
                    overpaid_msg = f"\n💡 You paid {overpaid_percentage:.0f}% ({actual_amount:.8f} {crypto_currency}) - thank you for the tip! 💝"
                
                # 发送 Telegram 通知给用户
                send_telegram_notification(
                    user_id,
                    f"💰 **Payment Successful!**\n\n"
                    f"💵 Amount: ${usd_amount} {currency}\n"
                    f"💎 Credits: +{credits}\n"
                    f"📋 Order: `{order_number}`{overpaid_msg}\n\n"
                    f"🎉 Your credits have been added!\n"
                    f"Use /balance to check your balance."
                )
                
                # 🔔 通知管理员（实时入账通知）
                overpaid_admin_msg = f"\n💰 Overpaid: {overpaid_percentage:.0f}%" if is_overpaid else ""
                notify_admin(
                    f"💰 **NEW SALE!** 💰\n\n"
                    f"👤 User: `{user_id}`\n"
                    f"💵 Amount: **${usd_amount} {currency}**\n"
                    f"💎 Credits: **{credits}**\n"
                    f"💳 Method: `Plisio ({crypto_currency})`{overpaid_admin_msg}\n"
                    f"📦 Package: `{package['name']}`\n"
                    f"📋 Order: `{order_number}`\n"
                    f"🆔 TXN: `{txn_id}`\n\n"
                    f"🎉 Cha-ching! 💸"
                )
                
                return jsonify({"status": "success", "credits_added": credits}), 200
            else:
                print(f"❌ Failed to add credits")
                return jsonify({"error": "Failed to add credits"}), 500
            
        elif status in ['error', 'cancelled', 'expired', 'cancelled duplicate']:
            
            # 通知用户
            status_messages = {
                'error': 'encountered an error',
                'cancelled': 'was cancelled',
                'expired': 'expired',
                'cancelled duplicate': 'was cancelled (duplicate)'
            }
            status_msg = status_messages.get(status, status)
            
            send_telegram_notification(
                user_id,
                f"❌ **Payment {status.title()}**\n\n"
                f"Your payment {status_msg}.\n"
                f"📋 Order: `{order_number}`\n\n"
                f"Please try again or contact support if you need help.\n"
                f"Use /support to contact admin."
            )
            
            return jsonify({"status": "ok"}), 200
        
        # 其他状态
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """统一的OpenAI兼容接口"""
    # 记录请求日志
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    
    # 验证API Key
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or auth_header.replace("Bearer ", "") != SERVER_AUTH_KEY:
        print(f"❌ Auth failed")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        data = request.json
        if not data:
            print(f"❌ 空请求体")
            return jsonify({"error": "Empty request body"}), 400
            
        model = data.get('model', '')
        messages = data.get('messages', [])
        stream = data.get('stream', False)
        
        if not messages:
            return jsonify({"error": "No messages provided"}), 400
    except Exception as e:
        print(f"❌ Invalid request: {e}")
        return jsonify({"error": "Invalid request"}), 400
    
    # 提取提示词和图片
    last_message = messages[-1]
    content = last_message.get('content', '')
    
    prompt_text = ""
    input_image_base64 = None
    
    if isinstance(content, str):
        prompt_text = content
    elif isinstance(content, list):
        for item in content:
            if item.get('type') == 'text':
                prompt_text += item.get('text', '') + " "
            elif item.get('type') == 'image_url':
                url = item.get('image_url', {}).get('url', '')
                if url.startswith('data:image'):
                    try:
                        input_image_base64 = url.split(',')[1]
                    except:
                        pass
    
    prompt_text = prompt_text.strip()
    
    # 判断服务类型
    if "video" in model.lower() or "wan" in model.lower():
        # 视频服务
        is_i2v = input_image_base64 is not None or "i2v" in model.lower() or "ImageToVideo" in model
        
        print(f"🎬 识别为视频服务 - {'图生视频' if is_i2v else '文生视频'}")
        
        if is_i2v:
            return handle_video_i2v(prompt_text, input_image_base64, model, stream, data)
        else:
            return handle_video_t2v(prompt_text, model, stream, data)
    else:
        # 图像服务
        return handle_image_generation(prompt_text, model, stream, data)

def handle_image_generation(prompt_text, model, stream, data):
    """处理图像生成 - 流式响应"""
    try:
        # 解析尺寸
        if "square" in model.lower():
            width, height = 1024, 1024
        elif "portrait" in model.lower():
            width, height = 832, 1216
        elif "landscape" in model.lower():
            width, height = 1216, 832
        else:
            width = data.get('width', 1024)
            height = data.get('height', 1024)
        
        
        # 创建工作流
        workflow = json.loads(json.dumps(IMAGE_WORKFLOW))
        workflow["3"]["inputs"]["seed"] = random.randint(1, 999999999999999)
        workflow["6"]["inputs"]["text"] = prompt_text
        workflow["13"]["inputs"]["width"] = width
        workflow["13"]["inputs"]["height"] = height
        
        # 提交到ComfyUI
        prompt_id = submit_to_comfyui(workflow)
        if not prompt_id:
            error_msg = f"ComfyUI连接失败。请检查: 1) ComfyUI是否运行 2) 地址配置: {COMFYUI_API_URL}"
            print(f"❌ {error_msg}")
            log_request("image", "failed", {"error": "Submit failed", "comfyui_url": COMFYUI_API_URL})
            return jsonify({"error": error_msg}), 500
        
        print(f"✅ 图像生成任务已提交: {prompt_id}")
        
        # 使用流式响应
        def generate_image_stream():
            response_id = f"chatcmpl-{prompt_id}"
            created_ts = int(time.time())
            
            # 发送初始消息
            initial_chunk = {
                'id': response_id,
                'object': 'chat.completion.chunk',
                'created': created_ts,
                'model': model,
                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '> 🎨 正在生成图片...\n\n'}, 'finish_reason': None}]
            }
            yield f"data: {json.dumps(initial_chunk, ensure_ascii=False)}\n\n"
            
            # 轮询等待完成
            start_time = time.time()
            timeout = 300  # 5分钟超时
            last_message = ""
            
            while time.time() - start_time < timeout:
                history = get_comfyui_history(prompt_id)
                if history and prompt_id in history:
                    prompt_history = history[prompt_id]
                    if "outputs" in prompt_history:
                        # 任务完成
                        outputs = prompt_history["outputs"]
                        output_url = None
                        
                        # 提取图片
                        for node_id, node_output in outputs.items():
                            if "images" in node_output:
                                images = node_output["images"]
                                if images:
                                    img = images[0]
                                    filename = img["filename"]
                                    subfolder = img.get("subfolder", "")
                                    
                                    # 下载图片
                                    image_data = get_comfyui_image(filename, subfolder)
                                    if image_data:
                                        # 保存到本地
                                        out_filename = f"{prompt_id}.png"
                                        out_path = os.path.join(IMAGES_DIR, out_filename)
                                        with open(out_path, "wb") as f:
                                            f.write(image_data)
                                        
                                        host = request.host_url.rstrip('/')
                                        output_url = f"{host}/files/images/{out_filename}"
                                        break
                        
                        if output_url:
                            log_request("image", "success", {"prompt_id": prompt_id})
                            
                            # 格式和图/comfyui_api_service.py保持一致
                            content = f"![image]({output_url})\n"
                            
                            # 发送最终结果
                            final_chunk = {
                                'id': response_id,
                                'object': 'chat.completion.chunk',
                                'created': created_ts,
                                'model': model,
                                'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': 'stop'}]
                            }
                            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                
                # 发送心跳保持连接
                elapsed = int(time.time() - start_time)
                if elapsed > 0 and elapsed % 5 == 0:
                    progress_msg = f"> 🎨 正在生成中 ({elapsed}秒)...\n"
                    if progress_msg != last_message:
                        progress_chunk = {
                            'id': response_id,
                            'object': 'chat.completion.chunk',
                            'created': created_ts,
                            'model': model,
                            'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]
                        }
                        yield f"data: {json.dumps(progress_chunk, ensure_ascii=False)}\n\n"
                        last_message = progress_msg
                
                time.sleep(2)
            
            # 超时
            log_request("image", "failed", {"error": "Timeout"})
            timeout_chunk = {
                'id': response_id,
                'object': 'chat.completion.chunk',
                'created': created_ts,
                'model': model,
                'choices': [{'index': 0, 'delta': {'content': '\n\n⏱️ 生成超时，请重试。'}, 'finish_reason': 'stop'}]
            }
            yield f"data: {json.dumps(timeout_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        
        # 返回流式响应（CORS头由@app.after_request统一处理）
        response = Response(stream_with_context(generate_image_stream()), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Connection'] = 'keep-alive'
        return response
        
    except Exception as e:
        log_request("image", "failed", {"error": str(e)})
        return jsonify({"error": f"生成失败: {str(e)}"}), 500

def handle_video_t2v(prompt_text, model, stream, data):
    """处理文生视频（竖屏）- 使用ComfyUI直连"""
    global t2v_count
    
    print(f"🎬 处理文生视频请求")
    
    # 检查并发限制
    with count_lock:
        current = t2v_count
    
    print(f"📊 当前并发: {current}/{MAX_CONCURRENT_T2V}")
    
    if not t2v_semaphore.acquire(blocking=False):
        print(f"❌ 并发已满，拒绝请求")
        log_request("video_t2v", "rejected", {"reason": "并发限制"})
        return jsonify({"error": f"文生视频服务繁忙，当前并发已达上限({MAX_CONCURRENT_T2V})"}), 429
    
    with count_lock:
        t2v_count += 1
    
    try:
        # 准备工作流
        workflow = json.loads(json.dumps(T2V_WORKFLOW))
        seed = random.randint(1, 999999999999999)
        
        # 🔇 简洁日志模式
        # print(f"🎲 文生视频随机种子: {seed}")
        
        # 更新工作流参数 - 正面提示词
        if "89" in workflow:
            workflow["89"]["inputs"]["text"] = prompt_text
        
        # 更新负面提示词 - 与ComfyUI工作流一致（T2V包含额外的"裸露，NSFW"）
        if "72" in workflow:
            workflow["72"]["inputs"]["text"] = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走，裸露，NSFW"
        
        # 更新视频尺寸为竖屏
        if "74" in workflow:
            workflow["74"]["inputs"]["width"] = 480
            workflow["74"]["inputs"]["height"] = 832
            workflow["74"]["inputs"]["length"] = 81
        
        # 更新随机种子 - 需要同时更新两个KSampler节点
        if "81" in workflow:
            workflow["81"]["inputs"]["noise_seed"] = seed
            # print(f"  → 节点81种子已更新: {seed}")
        if "78" in workflow:
            workflow["78"]["inputs"]["noise_seed"] = seed
            # print(f"  → 节点78种子已更新: {seed}")
        
        print(f"📤 提交到ComfyUI视频端点")
        
        # 提交任务到ComfyUI
        try:
            result = submit_video_to_comfyui(workflow)
            prompt_id = result.get("prompt_id")
            
            if not prompt_id:
                print(f"❌ ComfyUI返回无效的prompt_id")
                log_request("video_t2v", "failed", {"error": "No prompt_id"})
                return jsonify({"error": "ComfyUI提交失败：未获取到任务ID"}), 500
            
            print(f"✅ 文生视频任务已提交: {prompt_id}")
        except Exception as submit_error:
            print(f"❌ ComfyUI提交失败: {submit_error}")
            log_request("video_t2v", "failed", {"error": str(submit_error)})
            return jsonify({"error": f"ComfyUI提交失败: {str(submit_error)}"}), 500
        
        # 使用流式响应
        def generate_video_stream():
            response_id = f"chatcmpl-{prompt_id}"
            created_ts = int(time.time())
            
            # 发送初始消息
            initial_chunk = {
                'id': response_id,
                'object': 'chat.completion.chunk',
                'created': created_ts,
                'model': model,
                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '> 🚀 任务已提交，正在排队中...\n\n'}, 'finish_reason': None}]
            }
            yield f"data: {json.dumps(initial_chunk, ensure_ascii=False)}\n\n"
            
            # 轮询等待完成
            start_time = time.time()
            last_status = "IN_QUEUE"
            
            while time.time() - start_time < VIDEO_TIMEOUT:
                status_data = check_comfyui_video_status(prompt_id)
                if not status_data:
                    time.sleep(3)
                    # 发送心跳
                    keepalive_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]
                    }
                    yield f"data: {json.dumps(keepalive_chunk, ensure_ascii=False)}\n\n"
                    continue
                
                status = status_data.get("status")
                
                # 根据状态发送进度消息
                current_msg = ""
                if status == "IN_QUEUE":
                    current_msg = "> ⏳ 正在排队等待 GPU 资源...\n"
                elif status == "IN_PROGRESS":
                    current_msg = "> 🎬 正在生成视频 (预计 2-3 分钟)...\n"
                
                # 发送状态更新
                if status != last_status and current_msg:
                    status_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': current_msg}, 'finish_reason': None}]
                    }
                    yield f"data: {json.dumps(status_chunk, ensure_ascii=False)}\n\n"
                    last_status = status
                else:
                    # 发送心跳保持连接
                    keepalive_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]
                    }
                    yield f"data: {json.dumps(keepalive_chunk, ensure_ascii=False)}\n\n"
                
                if status == "COMPLETED":
                    outputs = status_data.get("outputs")
                    output_url = ""
                    
                    if outputs:
                        # 下载视频 - 与图片提取方式一致
                        video_data = download_comfyui_video(outputs)
                        if video_data:
                            out_filename = f"{prompt_id}.mp4"
                            out_path = os.path.join(IMAGES_DIR, out_filename)
                            with open(out_path, "wb") as f:
                                f.write(video_data)
                            
                            host = request.host_url.rstrip('/')
                            output_url = f"{host}/files/images/{out_filename}"
                    
                    log_request("video_t2v", "success", {"prompt_id": prompt_id})
                    
                    content = f"✅ 视频生成成功！\n\n🎬 [点击这里]({output_url})\n\n访问链接: {output_url}" if output_url else "⚠️ 生成完成但无法获取视频"
                    
                    # 发送最终结果
                    final_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': 'stop'}]
                    }
                    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                elif status == "FAILED":
                    log_request("video_t2v", "failed", {"status": status})
                    
                    fail_msg = '\n\n❌ 视频生成失败，请检查输入内容后重试。'
                    fail_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': fail_msg}, 'finish_reason': 'stop'}]
                    }
                    yield f"data: {json.dumps(fail_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                time.sleep(3)
            
            # 超时
            log_request("video_t2v", "failed", {"error": "Timeout"})
            timeout_chunk = {
                'id': response_id,
                'object': 'chat.completion.chunk',
                'created': created_ts,
                'model': model,
                'choices': [{'index': 0, 'delta': {'content': '\n\n⏱️ 任务超时（10分钟），请重试。'}, 'finish_reason': 'stop'}]
            }
            yield f"data: {json.dumps(timeout_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        
        # 返回流式响应（CORS头由@app.after_request统一处理）
        response = Response(stream_with_context(generate_video_stream()), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Connection'] = 'keep-alive'
        return response
        
    except Exception as e:
        log_request("video_t2v", "failed", {"error": str(e)})
        return jsonify({"error": f"生成失败: {str(e)}"}), 500
    finally:
        with count_lock:
            t2v_count -= 1
        t2v_semaphore.release()

def handle_video_i2v(prompt_text, input_image_base64, model, stream, data):
    """处理图生视频（竖屏）- 使用ComfyUI直连"""
    global i2v_count
    
    if not input_image_base64:
        return jsonify({"error": "图生视频需要提供图片"}), 400
    
    # 检查并发限制
    with count_lock:
        current = i2v_count
    
    if not i2v_semaphore.acquire(blocking=False):
        log_request("video_i2v", "rejected", {"reason": "并发限制"})
        return jsonify({"error": f"图生视频服务繁忙，当前并发已达上限({MAX_CONCURRENT_I2V})"}), 429
    
    with count_lock:
        i2v_count += 1
    
    try:
        # 准备工作流
        workflow = json.loads(json.dumps(I2V_WORKFLOW))
        seed = random.randint(1, 999999999999999)
        
        # 🔇 简洁日志模式
        # print(f"🎲 图生视频随机种子: {seed}")
        
        # 📝 图生视频使用工作流中固定的提示词，不再动态修改
        # 正面提示词（节点93）和负面提示词（节点89）保持工作流JSON中的原始值
        # print(f"  → 使用工作流固定提示词（不修改节点93和89）")
        
        # 更新视频尺寸为竖屏
        if "98" in workflow:
            workflow["98"]["inputs"]["width"] = 480
            workflow["98"]["inputs"]["height"] = 832
            workflow["98"]["inputs"]["length"] = 81
        
        # 🔥 关键修复：更新随机种子 - 需要同时更新两个KSampler节点
        if "86" in workflow:
            workflow["86"]["inputs"]["noise_seed"] = seed
            # print(f"  → 节点86种子已更新: {seed}")
        if "85" in workflow:
            workflow["85"]["inputs"]["noise_seed"] = seed
            # print(f"  → 节点85种子已更新: {seed}")
        
        # 保存输入图片到本地并上传到ComfyUI
        image_filename = f"i2v_input_{uuid.uuid4().hex}.png"
        image_path = os.path.join(IMAGES_DIR, image_filename)
        
        # 解码base64图片
        import base64
        image_data = base64.b64decode(input_image_base64)
        
        # 保存到本地（用于后续清理）
        with open(image_path, "wb") as f:
            f.write(image_data)
        
        # 上传图片到ComfyUI服务器
        uploaded_filename = upload_image_to_comfyui(image_data, image_filename)
        
        # 更新工作流中的图片引用
        if "97" in workflow:
            workflow["97"]["inputs"]["image"] = uploaded_filename
        
        # 提交任务到ComfyUI
        try:
            result = submit_video_to_comfyui(workflow)
            prompt_id = result.get("prompt_id")
        
            if not prompt_id:
                log_request("video_i2v", "failed", {"error": "No prompt_id"})
                return jsonify({"error": "ComfyUI提交失败"}), 500
        
            print(f"✅ 图生视频任务已提交: {prompt_id}")
        except Exception as submit_error:
            print(f"❌ ComfyUI提交失败: {submit_error}")
            log_request("video_i2v", "failed", {"error": str(submit_error)})
            return jsonify({"error": f"ComfyUI提交失败: {str(submit_error)}"}), 500
        
        # 使用流式响应
        def generate_i2v_stream():
            response_id = f"chatcmpl-{prompt_id}"
            created_ts = int(time.time())
            
            # 发送初始消息
            initial_chunk = {
                'id': response_id,
                'object': 'chat.completion.chunk',
                'created': created_ts,
                'model': model,
                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '> 🚀 任务已提交，正在排队中...\n\n'}, 'finish_reason': None}]
            }
            yield f"data: {json.dumps(initial_chunk, ensure_ascii=False)}\n\n"
            
            # 轮询等待完成
            start_time = time.time()
            last_status = "IN_QUEUE"
            
            while time.time() - start_time < VIDEO_TIMEOUT:
                status_data = check_comfyui_video_status(prompt_id)
                if not status_data:
                    time.sleep(3)
                    # 发送心跳
                    keepalive_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]
                    }
                    yield f"data: {json.dumps(keepalive_chunk, ensure_ascii=False)}\n\n"
                    continue
                
                status = status_data.get("status")
                
                # 根据状态发送进度消息
                current_msg = ""
                if status == "IN_QUEUE":
                    current_msg = "> ⏳ 正在排队等待 GPU 资源...\n"
                elif status == "IN_PROGRESS":
                    current_msg = "> 🎬 正在生成视频 (预计 2-3 分钟)...\n"
                
                # 发送状态更新
                if status != last_status and current_msg:
                    status_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': current_msg}, 'finish_reason': None}]
                    }
                    yield f"data: {json.dumps(status_chunk, ensure_ascii=False)}\n\n"
                    last_status = status
                else:
                    # 发送心跳保持连接
                    keepalive_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]
                    }
                    yield f"data: {json.dumps(keepalive_chunk, ensure_ascii=False)}\n\n"
                
                if status == "COMPLETED":
                    outputs = status_data.get("outputs")
                    output_url = ""
                    
                    if outputs:
                        # 下载视频 - 与图片提取方式一致
                        video_data = download_comfyui_video(outputs)
                        if video_data:
                            out_filename = f"{prompt_id}.mp4"
                            out_path = os.path.join(IMAGES_DIR, out_filename)
                            with open(out_path, "wb") as f:
                                f.write(video_data)
                            
                            host = request.host_url.rstrip('/')
                            output_url = f"{host}/files/images/{out_filename}"
                    
                    log_request("video_i2v", "success", {"prompt_id": prompt_id})
                    
                    content = f"✅ 视频生成成功！\n\n🎬 [点击这里]({output_url})\n\n访问链接: {output_url}" if output_url else "⚠️ 生成完成但无法获取视频"
                    
                    # 发送最终结果
                    final_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': 'stop'}]
                    }
                    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                elif status == "FAILED":
                    log_request("video_i2v", "failed", {"status": status})
                    
                    fail_msg = '\n\n❌ 视频生成失败，请检查输入内容后重试。'
                    fail_chunk = {
                        'id': response_id,
                        'object': 'chat.completion.chunk',
                        'created': created_ts,
                        'model': model,
                        'choices': [{'index': 0, 'delta': {'content': fail_msg}, 'finish_reason': 'stop'}]
                    }
                    yield f"data: {json.dumps(fail_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                time.sleep(3)
            
            # 超时
            log_request("video_i2v", "failed", {"error": "Timeout"})
            timeout_chunk = {
                'id': response_id,
                'object': 'chat.completion.chunk',
                'created': created_ts,
                'model': model,
                'choices': [{'index': 0, 'delta': {'content': '\n\n⏱️ 任务超时（10分钟），请重试。'}, 'finish_reason': 'stop'}]
            }
            yield f"data: {json.dumps(timeout_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        
        # 返回流式响应（CORS头由@app.after_request统一处理）
        response = Response(stream_with_context(generate_i2v_stream()), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Connection'] = 'keep-alive'
        return response
        
    except Exception as e:
        log_request("video_i2v", "failed", {"error": str(e)})
        return jsonify({"error": f"生成失败: {str(e)}"}), 500
    finally:
        # 清理临时图片文件
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
        except:
            pass
        
        with count_lock:
            i2v_count -= 1
        i2v_semaphore.release()

if __name__ == '__main__':
    import logging
    
    # 🔇 生产环境：设置简洁日志
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)  # 只显示警告和错误
    
    print("="*60)
    print("🚀 Lili AI Service - Production Mode")
    print("="*60)
    print(f"🌐 Port: 5010")
    print(f"📁 Files: {IMAGES_DIR}")
    print("="*60)
    
    app.run(host='0.0.0.0', port=5010, threaded=True)

