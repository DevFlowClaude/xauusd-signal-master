# VPS Deployment Guide - Windows Server

Step-by-step guide to deploy XAUUSD Signal Master on a Windows VPS with
FxPro MetaTrader 5.

## Recommended VPS Specs

| Provider | Plan | Price | Specs |
|----------|------|-------|-------|
| **Contabo** | VPS M Windows | €3.99/month | 4 vCPU, 8GB RAM, 200GB SSD |
| ForexVPS | VPS Forex Trader | $20/month | Low-latency NY4, forex-optimized |
| AWS EC2 | t3.medium Windows | ~$30/month | 2 vCPU, 4GB, elastic |

**Recommendation: Contabo VPS M Windows**. Best price/performance for 24/7 
operation. Forex VPS premium pricing only justified for scalping strategies 
(not needed for M5).

## Step 1: Provision the VPS

1. Order at https://contabo.com/en/vps/
2. Choose: VPS M, Windows Server 2022, location close to you (Germany is fine 
   for EU, NY4 if FxPro server is US)
3. Note the IP address and admin password

## Step 2: Connect via RDP

On Windows: `mstsc.exe`
On Mac/Linux: use Microsoft Remote Desktop app

```
Host: YOUR_VPS_IP
User: Administrator
Password: provided in setup email
```

## Step 3: Install dependencies

### Python 3.10+

Download from https://www.python.org/downloads/
During install: check "Add Python to PATH"

Verify:
```cmd
python --version
pip --version
```

### Git

Download from https://git-scm.com/download/win

### MetaTrader 5

Download FxPro MT5 from https://www.fxpro.com/trading-platforms/metatrader-5
Install and log in with your FxPro account credentials.

## Step 4: Clone and setup the project

Open PowerShell or CMD:

```cmd
cd C:\
mkdir trading
cd trading

# Clone the Sunrise Ogle strategy (required)
git clone https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd.git sunrise_ogle

# Copy your signal master project here (either clone from your own repo,
# or copy via RDP file transfer)
# Example if you have it in git:
# git clone YOUR_REPO xauusd_signal_master

cd xauusd_signal_master

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Step 5: Configure

### Create .env file

```cmd
copy .env.example .env
notepad .env
```

Fill in:
```
TELEGRAM_BOT_TOKEN=your_token_from_botfather
TELEGRAM_CHAT_ID=your_chat_id
XAUUSD_MASTER_SECRET=RANDOM_32_CHAR_STRING_HERE
MT5_LOGIN=12345678
MT5_PASSWORD=your_mt5_password
MT5_SERVER=FxPro-MT5
```

### Edit config.yaml if needed

```cmd
notepad master\config.yaml
```

Key settings to review:
- `default_risk_percent: 1.5`
- `fundingpips_guards:` thresholds
- `broadcaster:` port numbers

### Firewall rules

Open the required ports:

```cmd
netsh advfirewall firewall add rule name="XAUUSD Master WebSocket" dir=in action=allow protocol=TCP localport=8765
netsh advfirewall firewall add rule name="XAUUSD Master HTTP" dir=in action=allow protocol=TCP localport=8766
```

If clients connect from other locations (not just this VPS), make sure 
the VPS hosting provider also allows these ports at network level.

## Step 6: Test run

Make sure MT5 terminal is running and logged in. Then:

```cmd
venv\Scripts\activate
python -m master.engine
```

Expected output:
```
2026-xx-xx XX:XX | INFO | master_engine | XAUUSD Signal Master v1.0.0
2026-xx-xx XX:XX | INFO | mt5_feed | MT5 connected: terminal=MetaTrader 5 ...
2026-xx-xx XX:XX | INFO | mt5_feed | Account: #12345678 ... balance=10000.00 USD
2026-xx-xx XX:XX | INFO | broadcaster | WebSocket server listening on ws://0.0.0.0:8765
2026-xx-xx XX:XX | INFO | http_api | HTTP API listening on http://0.0.0.0:8766
2026-xx-xx XX:XX | INFO | master_engine | Master engine fully started.
```

Verify HTTP endpoint works from another machine:
```
curl http://YOUR_VPS_IP:8766/health
# Should return: {"status":"ok","timestamp":...}
```

## Step 7: Run as a Windows Service (production)

For 24/7 operation, use **NSSM** (Non-Sucking Service Manager) to run as 
a Windows service that auto-starts on boot.

### Install NSSM

Download from https://nssm.cc/download
Extract `nssm.exe` to `C:\Windows\System32\`

### Create the service

```cmd
nssm install XAUUSDMaster "C:\trading\xauusd_signal_master\venv\Scripts\python.exe"
```

In the NSSM GUI:
- **Application** tab:
  - Path: `C:\trading\xauusd_signal_master\venv\Scripts\python.exe`
  - Startup directory: `C:\trading\xauusd_signal_master`
  - Arguments: `-m master.engine`
- **I/O** tab:
  - Output: `C:\trading\xauusd_signal_master\logs\service_stdout.log`
  - Error: `C:\trading\xauusd_signal_master\logs\service_stderr.log`
- **Exit actions** tab:
  - Default action: Restart application
- **Details** tab:
  - Startup type: Automatic

Click "Install service".

### Start the service

```cmd
net start XAUUSDMaster
```

Check it's running:
```cmd
sc query XAUUSDMaster
```

### Service management

```cmd
net stop XAUUSDMaster       # Stop
net start XAUUSDMaster      # Start
nssm edit XAUUSDMaster      # Edit settings
nssm remove XAUUSDMaster    # Uninstall
```

## Step 8: Auto-start MT5 terminal

MT5 must be running for the master to work. To ensure it starts on boot:

1. Start MT5, log in, enable AutoLogin
2. Create a shortcut to `terminal64.exe` in:
   `C:\Users\Administrator\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`

Or use Task Scheduler:
1. Open Task Scheduler
2. Create Basic Task "Start MT5"
3. Trigger: "When the computer starts"
4. Action: Start program `C:\Program Files\FxPro - MetaTrader 5\terminal64.exe`

## Step 9: Dashboard (optional)

The Streamlit dashboard can run on the same VPS or your local machine 
(reading the JSON files over a shared drive or via HTTP).

For remote access, start:
```cmd
streamlit run master\integrations\dashboard.py --server.port 8501 --server.address 0.0.0.0
```

Add firewall rule:
```cmd
netsh advfirewall firewall add rule name="XAUUSD Dashboard" dir=in action=allow protocol=TCP localport=8501
```

Access at: `http://YOUR_VPS_IP:8501`

**Warning**: the default Streamlit server has no auth. Use a reverse proxy 
with HTTP basic auth (nginx) for production, or bind to 127.0.0.1 only and 
SSH-tunnel to access.

## Step 10: MT5 EA Client setup

### On the same VPS (or on your FundingPips account VPS):

1. Copy `clients/mt5_ea/XAUUSD_Signal_Client.mq5` to:
   `C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\<TERMINAL_ID>\MQL5\Experts\`

2. Open MetaEditor, compile (F7). No errors expected.

3. In MT5, open Tools > Options > Expert Advisors:
   - Check "Allow algorithmic trading"
   - Check "Allow WebRequest for listed URL"
   - Add URL: `http://127.0.0.1:8766` (if same VPS) or `http://MASTER_VPS_IP:8766`

4. Drag the EA onto a XAUUSD chart.

5. In the EA parameters dialog, configure:
   - `InpMasterURL`: your master's URL
   - `InpClientId`: unique (e.g. "fundingpips_10k_kokkli")
   - `InpSymbol`: broker's XAUUSD symbol name
   - `InpRiskOverride`: 0 (use master's 1.5%) or lower to cap

6. Click OK. The EA's emoji will appear in the top-right corner of the chart.
   It should show a smiley face indicator.

7. Monitor in Experts tab of MT5 Terminal panel.

## Monitoring

### Log files

Location: `C:\trading\xauusd_signal_master\logs\`
- `master_engine.log` - main orchestrator
- `broadcaster.log` - WebSocket + JSON signals
- `mt5_feed.log` - MT5 connection
- `fundingpips_guards.log` - guard blocks
- `telegram.log` - notification delivery

Files rotate at 10MB, keep last 10 backups.

### Health checks

Quick check from any machine:
```
curl http://YOUR_VPS_IP:8766/health
curl http://YOUR_VPS_IP:8766/status
```

### Telegram

If configured, you'll receive:
- Startup notification
- Each new signal
- Daily P&L summary
- Guard block alerts
- Shutdown notification

## Troubleshooting

### "MetaTrader5 package not installed"
On Linux/Mac development, MT5 package isn't available. The master will 
use MockMT5Feed (CSV replay). On Windows VPS:
```cmd
pip install MetaTrader5
```

### "Symbol XAUUSD not available"
Check your broker's symbol name. FxPro uses `XAUUSD`, some others use 
`GOLD` or `XAUUSDm`. Edit `config.yaml`:
```yaml
mt5:
  symbol_candidates:
    - "XAUUSD"
    - "GOLD"
    - "YourBrokerSymbol"
```

### "WebRequest error" in MT5 EA
Ensure the master URL is whitelisted in MT5's Expert Advisor settings.

### "Guards permanently disabled"
You hit the overall loss limit. To reset (e.g. after passing a challenge 
and starting fresh), delete `data/guards_state.json` while the master is 
stopped, then restart.

### Master not picking up new bars
- Check MT5 is actually running and logged in
- Verify time zone settings (should be UTC/GMT)
- Check `logs/mt5_feed.log` for connection errors

## Performance & Cost

- **VPS cost**: €4/month (Contabo)
- **FxPro demo**: free
- **FundingPips $10K 2-Step**: $32 one-time challenge fee
- **Total monthly cost**: €4 + your time

## Next Steps

Once the system is running on VPS:

1. Run in demo mode for 2-4 weeks
2. Validate signals match backtest logic
3. Review Telegram output daily
4. If performance matches expectations, attempt FundingPips challenge
5. If successful, grow account size by passing more challenges

Good luck!
