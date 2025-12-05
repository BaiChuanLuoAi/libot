import subprocess
import os

os.chdir('d:/libot')

commands = [
    'git log --oneline -3',
    'git remote -v',
    'git ls-files | findstr config',
    'git status'
]

for cmd in commands:
    print(f"\n{'='*60}")
    print(f"Running: {cmd}")
    print('='*60)
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
    except Exception as e:
        print(f"Error: {e}")
