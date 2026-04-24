//+------------------------------------------------------------------+
//| XAUUSD_Signal_Client.mq5                                         |
//| Signal subscriber EA for MXPRO (GOLD)                            |
//| v1.03 - Robust INIT, Auto Filling Mode, Detailed Diagnostics     |
//+------------------------------------------------------------------+
#property copyright "XAUUSD Signal Master"
#property version   "1.03"
#property description "Pulls signals from Python Master via HTTP REST API"

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>
#include <Trade/SymbolInfo.mqh>

// -------------------- Input parameters --------------------
input string  InpMasterURL       = "http://127.0.0.1:8766";
input string  InpClientId        = "client_demo_01";
input string  InpApiKey          = "";
input string  InpSymbol          = "GOLD";                 // Leave empty to use chart symbol
input int     InpMagicNumber     = 20260419;
input int     InpPollIntervalSec = 2;
input double  InpRiskOverride    = 0.0;
input double  InpMaxLotSize      = 0.0;
input bool    InpAllowLong       = true;
input bool    InpAllowShort      = true;
input bool    InpCloseOnDeinit   = false;
input int     InpMaxSlippagePoints = 30;
input int     InpSignatureTimeout = 600;
input bool    InpSkipHealthCheck = false;                  // Set true to bypass /health test

// -------------------- Globals --------------------
CTrade         trade;
CPositionInfo  positionInfo;
CSymbolInfo    symbolInfo;
string         g_symbol = "";

string   g_lastSignalId = "";
datetime g_lastPollTime = 0;
int      g_errorCount = 0;
int      g_successCount = 0;

// -------------------- Forward declarations --------------------
void ReportAck(string signalId, string status, double price, double lots, int ticket, string errorMsg);
ENUM_ORDER_TYPE_FILLING GetBestFillingMode(string symbol);

// -------------------- Lifecycle --------------------
int OnInit()
{
   PrintFormat("=== INIT START | Build %d ===", TerminalInfoInteger(TERMINAL_BUILD));
   
   // 1. Symbol Resolution
   g_symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   if(!SymbolSelect(g_symbol, true))
   {
      PrintFormat("❌ SymbolSelect FAILED for '%s'. Falling back to chart: %s", g_symbol, _Symbol);
      g_symbol = _Symbol;
      if(!SymbolSelect(g_symbol, true))
      {
         PrintFormat("❌ CRITICAL: No valid symbol available!");
         return INIT_FAILED;
      }
   }
   PrintFormat("✅ Symbol resolved: %s", g_symbol);

   if(!symbolInfo.Name(g_symbol) || !symbolInfo.RefreshRates())
   {
      PrintFormat("❌ CRITICAL: Failed to refresh symbol info");
      return INIT_FAILED;
   }

   PrintFormat("✅ Symbol OK | Point: %G | Digits: %d | MinLot: %.2f | TickValue: %.2f", 
               g_symbol, symbolInfo.Point(), (int)symbolInfo.Digits(), symbolInfo.LotsMin(), symbolInfo.TickValue());

   // 2. Trade Object Setup
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpMaxSlippagePoints);
   trade.SetAsyncMode(false);
   
   // Auto-detect supported filling mode
   ENUM_ORDER_TYPE_FILLING filling = GetBestFillingMode(g_symbol);
   trade.SetTypeFilling(filling);
   PrintFormat("✅ Order Filling Mode: %s", 
               (filling==ORDER_FILLING_FOK) ? "FOK" : 
               (filling==ORDER_FILLING_IOC) ? "IOC" : "RETURN");

   PrintFormat("📊 Account: #%d | Balance: %.2f %s | Broker: %s",
               AccountInfoInteger(ACCOUNT_LOGIN), AccountInfoDouble(ACCOUNT_BALANCE),
               AccountInfoString(ACCOUNT_CURRENCY), AccountInfoString(ACCOUNT_COMPANY));

   // 3. WebRequest Test
   if(!InpSkipHealthCheck)
   {
      string headers = "";
      uchar data[], result[];
      string res_headers;
      int timeout = 5000;
      string testUrl = InpMasterURL + "/health";
      
      PrintFormat("🔌 Testing WebRequest: %s", testUrl);
      int res = WebRequest("GET", testUrl, headers, timeout, data, result, res_headers);
      
      if(res == -1)
      {
         int err = GetLastError();
         PrintFormat("❌ WebRequest FAILED! Error: %d", err);
         
         if(err == 4001) PrintFormat("   → FIX: Enable 'Allow WebRequest' in MT5 Options");
         else if(err == 4010) PrintFormat("   → FIX: Add EXACTLY '%s' to WebRequest URL list", InpMasterURL);
         else if(err == 4003 || err == 4006) PrintFormat("   → FIX: Start Python Master server or check firewall");
         
         PrintFormat("   🔄 Restart MT5 after changing WebRequest settings!");
         return INIT_FAILED;
      }
      if(res != 200)
      {
         PrintFormat("❌ Master returned HTTP %d. Response: %s", res, CharArrayToString(result));
         return INIT_FAILED;
      }
      PrintFormat("✅ Master connected: %s", CharArrayToString(result));
   }
   else
   {
      PrintFormat("⚠️ Health check skipped (InpSkipHealthCheck=true)");
   }

   EventSetTimer(InpPollIntervalSec);
   PrintFormat("🎯 INIT SUCCESSFUL. Polling every %d sec.", InpPollIntervalSec);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(InpCloseOnDeinit) CloseAllManaged();
   PrintFormat("=== EA removed. Success: %d | Errors: %d ===", g_successCount, g_errorCount);
}

// -------------------- Main polling loop --------------------
void OnTimer()
{
   if(TimeCurrent() - g_lastPollTime < InpPollIntervalSec) return;
   g_lastPollTime = TimeCurrent();

   string json = FetchLatestSignal();
   if(json == "" || json == "null") return;
   ProcessSignalJson(json);
}

// -------------------- HTTP request --------------------
string FetchLatestSignal()
{
   string url = InpMasterURL + "/signals/latest";
   string headers = "";
   if(InpApiKey != "")
   {
      headers = "X-API-Key: " + InpApiKey + "\r\n";
      headers += "X-Client-ID: " + InpClientId + "\r\n";
   }

   uchar data[], result[];
   string res_headers;
   int timeout = 5000;

   int status = WebRequest("GET", url, headers, timeout, data, result, res_headers);
   if(status == -1)
   {
      g_errorCount++;
      if(g_errorCount % 10 == 1) PrintFormat("⚠️ WebRequest error %d", GetLastError());
      return "";
   }
   if(status != 200) { PrintFormat("⚠️ HTTP %d", status); return ""; }

   g_successCount++;
   return CharArrayToString(result);
}

// -------------------- JSON parsing --------------------
string ExtractString(const string &json, const string &key)
{
   string pat = "\"" + key + "\":\"";
   int s = StringFind(json, pat);
   if(s < 0) return "";
   s += StringLen(pat);
   int e = StringFind(json, "\"", s);
   if(e < 0) return "";
   return StringSubstr(json, s, e - s);
}

double ExtractNumber(const string &json, const string &key)
{
   string pat = "\"" + key + "\":";
   int s = StringFind(json, pat);
   if(s < 0) return 0.0;
   s += StringLen(pat);
   while(s < StringLen(json) && StringGetCharacter(json, s) == ' ') s++;
   int e = s;
   while(e < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, e);
      if(ch == ',' || ch == '}' || ch == ' ' || ch == '\n' || ch == '\r') break;
      e++;
   }
   string val = StringSubstr(json, s, e - s);
   return (val == "null") ? 0.0 : StringToDouble(val);
}

// -------------------- Signal processing --------------------
void ProcessSignalJson(const string &json)
{
   int sigStart = StringFind(json, "\"signal\":");
   if(sigStart < 0) return;
   int objStart = StringFind(json, "{", sigStart);
   if(objStart < 0) return;

   string sigStr = StringSubstr(json, objStart);
   string sigId = ExtractString(sigStr, "signal_id");
   if(sigId == "" || sigId == g_lastSignalId) return;

   string sigType = ExtractString(sigStr, "signal_type");
   double entryPx = ExtractNumber(sigStr, "entry_price");
   double sl      = ExtractNumber(sigStr, "stop_loss");
   double tp      = ExtractNumber(sigStr, "take_profit");
   double riskPct = ExtractNumber(sigStr, "risk_percent");
   double ts      = ExtractNumber(sigStr, "timestamp");

   if(ts > 0 && (TimeCurrent() - (datetime)ts) > InpSignatureTimeout)
   {
      PrintFormat("⏳ Signal %s expired", sigId);
      g_lastSignalId = sigId;
      return;
   }

   PrintFormat("📡 SIGNAL: id=%s type=%s entry=%.2f SL=%.2f TP=%.2f risk=%.2f%%",
               sigId, sigType, entryPx, sl, tp, riskPct);
   g_lastSignalId = sigId;

   double useRisk = riskPct;
   if(InpRiskOverride > 0 && InpRiskOverride < riskPct)
   {
      useRisk = InpRiskOverride;
      PrintFormat("   Risk capped to %.2f%%", useRisk);
   }

   bool isLong  = (sigType == "OPEN_LONG");
   bool isShort = (sigType == "OPEN_SHORT");
   bool isClose = (sigType == "CLOSE_ALL");

   if(isClose) { CloseAllManaged(); ReportAck(sigId, "FILLED", 0, 0, 0, ""); return; }
   if(isLong && !InpAllowLong)  { PrintFormat("   🚫 LONG blocked"); ReportAck(sigId, "REJECTED", 0, 0, 0, "LONG disabled"); return; }
   if(isShort && !InpAllowShort){ PrintFormat("   🚫 SHORT blocked"); ReportAck(sigId, "REJECTED", 0, 0, 0, "SHORT disabled"); return; }
   if(!isLong && !isShort) { PrintFormat("   ⚠️ Unknown type: %s", sigType); return; }
   if(HaveManagedPosition()) { PrintFormat("   ⏳ Position already open"); ReportAck(sigId, "REJECTED", 0, 0, 0, "already open"); return; }

   ExecuteTrade(sigId, isLong, sl, tp, useRisk);
}

void ExecuteTrade(const string &signalId, bool isLong, double sl, double tp, double riskPercent)
{
   symbolInfo.RefreshRates();
   double entry = isLong ? symbolInfo.Ask() : symbolInfo.Bid();
   double lot = CalculateLotSize(entry, sl, riskPercent);
   if(lot <= 0) { PrintFormat("   ❌ Lot calc failed"); ReportAck(signalId, "REJECTED", 0, 0, 0, "lot calc failed"); return; }

   if(InpMaxLotSize > 0 && lot > InpMaxLotSize) lot = InpMaxLotSize;

   int digits = (int)symbolInfo.Digits();
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   string comment = "XAUUSD_Master_" + StringSubstr(signalId, 0, 8);
   bool ok = isLong ? trade.Buy(lot, g_symbol, 0, sl, tp, comment) 
                    : trade.Sell(lot, g_symbol, 0, sl, tp, comment);

   if(!ok)
   {
      int rc = (int)trade.ResultRetcode();
      PrintFormat("   ❌ TRADE FAILED: %s (code %d)", trade.ResultRetcodeDescription(), rc);
      ReportAck(signalId, "REJECTED", 0, 0, 0, "retcode=" + IntegerToString(rc));
      return;
   }

   PrintFormat("   ✅ TRADE OK: #%d %.2f lots @ %.2f", (int)trade.ResultOrder(), trade.ResultVolume(), trade.ResultPrice());
   ReportAck(signalId, "FILLED", trade.ResultPrice(), trade.ResultVolume(), (int)trade.ResultOrder(), "");
}

double CalculateLotSize(double entry, double sl, double riskPct)
{
   if(entry <= 0 || sl <= 0 || riskPct <= 0) return 0.0;
   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * riskPct / 100.0;
   double slPts = MathAbs(entry - sl) / symbolInfo.Point();
   if(slPts <= 0) return 0.0;

   double valPerPtPerLot = symbolInfo.TickValue() * (symbolInfo.Point() / symbolInfo.TickSize());
   if(valPerPtPerLot <= 0) return 0.0;

   double lotsRaw = riskMoney / (slPts * valPerPtPerLot);
   double step = symbolInfo.LotsStep();
   double lots = MathFloor(lotsRaw / step) * step;
   lots = MathMax(symbolInfo.LotsMin(), MathMin(lots, symbolInfo.LotsMax()));

   double margin = 0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, g_symbol, lots, entry, margin)) return 0.0;
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(margin > free * 0.80)
   {
      double ratio = (free * 0.80) / margin;
      double safe = MathFloor(lots * ratio / step) * step;
      if(safe < symbolInfo.LotsMin()) return 0.0;
      PrintFormat("   📉 Margin limit: %.2f -> %.2f", lots, safe);
      return safe;
   }
   return lots;
}

// -------------------- Position management --------------------
bool HaveManagedPosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(positionInfo.SelectByIndex(i) && 
         positionInfo.Magic() == InpMagicNumber && 
         positionInfo.Symbol() == g_symbol) return true;
   }
   return false;
}

void CloseAllManaged()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i) && 
         positionInfo.Magic() == InpMagicNumber && 
         positionInfo.Symbol() == g_symbol)
      {
         bool ok = trade.PositionClose(positionInfo.Ticket());
         PrintFormat("🔒 Close #%d: %s", (int)positionInfo.Ticket(), ok ? "OK" : "FAIL");
      }
   }
}

// -------------------- ACK reporting --------------------
void ReportAck(string signalId, string status, double price, double lots, int ticket, string errorMsg)
{
   string url = InpMasterURL + "/ack";
   string headers = "Content-Type: application/json\r\n";
   if(InpApiKey != "")
   {
      headers += "X-API-Key: " + InpApiKey + "\r\n";
      headers += "X-Client-ID: " + InpClientId + "\r\n";
   }

   string body = StringFormat(
      "{\"signal_id\":\"%s\",\"client_id\":\"%s\",\"timestamp\":%d,"
      "\"status\":\"%s\",\"executed_price\":%.2f,\"executed_lots\":%.2f,"
      "\"ticket\":%d,\"error_message\":\"%s\","
      "\"account_balance\":%.2f,\"account_equity\":%.2f}",
      signalId, InpClientId, (int)TimeCurrent(),
      status, price, lots, ticket, errorMsg,
      AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY)
   );

   uchar post[];
   uchar result[];
   string res_headers;
   StringToCharArray(body, post);
   int res = WebRequest("POST", url, headers, 5000, post, result, res_headers);
   if(res != 200) PrintFormat("⚠️ ACK failed: HTTP %d", res);
}

// -------------------- Helpers & Events --------------------
ENUM_ORDER_TYPE_FILLING GetBestFillingMode(string symbol)
{
   long filling = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

void OnTick() {}
void OnTrade() {}
void OnTradeTransaction(const MqlTradeTransaction& trans, const MqlTradeRequest& req, const MqlTradeResult& res) {}