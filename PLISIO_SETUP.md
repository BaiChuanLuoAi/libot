# 💰 Plisio Payment Setup Guide

## ✅ Configuration Complete!

Your bot is now configured to accept crypto payments via Plisio!

## 📋 Current Configuration

- **Secret Key**: `thVUIhE1kmdI2mwDT5zMhxajeR-GzULvz5uVsTitkYxtPdWYudcCnSWYQcpEErht`
- **Domain**: `https://www.lilibot.top`
- **Webhook URL**: `https://www.lilibot.top/webhooks/plisio`

## 🔧 Plisio Dashboard Settings

Make sure your Plisio merchant account has these settings:

### 1. Status URL (Callback URL)
```
https://www.lilibot.top/webhooks/plisio
```

### 2. Allowed Cryptocurrencies
- ✅ BTC (Bitcoin)
- ✅ ETH (Ethereum)
- ✅ USDT (Tether)
- ✅ XMR (Monero)
- ✅ LTC (Litecoin)
- ✅ And more...

### 3. White Label Settings (Optional)
- Enable white label for custom branding
- Add your logo/colors in Plisio dashboard

### 4. High-Risk Acceptance
- ✅ Enable "Accept high-risk payments" if you're selling adult content

## 🚀 How It Works

### User Flow:
1. User types `/buy` in Telegram
2. Bot shows "Pay with Crypto" button
3. User clicks button → Bot creates Plisio invoice
4. User is redirected to Plisio payment page
5. User selects cryptocurrency (BTC/ETH/USDT/etc.)
6. User sends payment to provided address
7. Plisio confirms payment → Webhook notifies your server
8. Bot automatically adds 100 credits to user account
9. User receives Telegram notification

### Payment Flow:
```
[User] → [Telegram Bot] → [Plisio API] → [Payment Page]
                                ↓
                          [Blockchain]
                                ↓
                          [Plisio Webhook] → [Your Server] → [Database]
                                                    ↓
                                            [Telegram Notification]
```

## 📁 Files Modified

1. **`tg_bot/bot.py`**
   - Added Plisio payment creation logic
   - Replaced old crypto payment callback
   - Updated `/buy` command to show Plisio option

2. **`server.py`**
   - Added `/webhooks/plisio` endpoint
   - Signature verification for security
   - Automatic credit addition on payment success
   - Telegram notifications

3. **Configuration Files**
   - `tg_bot/config.env` - Added Plisio Secret Key
   - `env.example` - Updated template

## 🧪 Testing

### Test Small Payment (Recommended):
```python
# In bot.py, temporarily change amount:
amount = "0.50"  # Test with $0.50 instead of $9.99
```

### Test Flow:
1. Start bot: `python tg_bot/bot.py`
2. Type `/buy` in Telegram
3. Click "Pay with Crypto"
4. Select cryptocurrency
5. Send test payment
6. Check webhook logs in server.py
7. Verify credits are added

### Check Webhook Logs:
```bash
# Server logs will show:
📥 Plisio webhook received: {...}
✅ Added 100 credits to user 123456789
```

## 🔒 Security Features

1. **Signature Verification**
   - All webhooks are verified using SHA1 hash
   - Invalid signatures are rejected

2. **Duplicate Prevention**
   - Payment IDs are checked before processing
   - Prevents double-crediting

3. **Environment Variables**
   - Secret key stored in config files (not in code)
   - .env files are gitignored

## 💡 Pricing Configuration

Current setup:
- **Price**: $9.99 USD
- **Credits**: 100
- **Conversion**: $1 = 10 credits

To change pricing, edit in `tg_bot/bot.py`:
```python
async def plisio_payment_callback(...):
    amount = "9.99"  # Change this
    # ...
    credits = 100  # And this in database.py webhook
```

## 🌐 Supported Cryptocurrencies

Plisio supports 100+ cryptocurrencies including:

| Coin | Name | Typical Confirmation |
|------|------|---------------------|
| BTC | Bitcoin | 10-60 min |
| ETH | Ethereum | 2-5 min |
| USDT | Tether (TRC20) | 1-2 min |
| USDT | Tether (ERC20) | 2-5 min |
| XMR | Monero | 10-20 min |
| LTC | Litecoin | 5-15 min |
| TRX | Tron | 1-2 min |
| BNB | Binance Coin | 1-2 min |

## 📞 Support

If users have payment issues:

1. **Check Plisio Dashboard**
   - Login to merchant.plisio.net
   - View recent invoices
   - Check payment status

2. **Check Server Logs**
   - Look for webhook calls
   - Verify signature validation

3. **Manual Credit Addition**
   - Admin can use: `/add_credits [user_id] [amount]`

## 🎉 Ready to Go!

Your bot is fully configured and ready to accept crypto payments!

To start earning:
```bash
# Terminal 1: Start API server
python server.py

# Terminal 2: Start Telegram bot
cd tg_bot
python bot.py
```

Your bot is now live and can accept payments! 🚀💰
