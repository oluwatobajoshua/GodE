//+------------------------------------------------------------------+
//| CanvasHUD.mqh — Flicker-free CCanvas graphical overlay            |
//| Real-time state, market context, active trading plan             |
//+------------------------------------------------------------------+
#property strict

#include <Canvas.mqh>

enum ENUM_HUD_BADGE
   {
   BADGE_CLEARED = 0,
   BADGE_BLOCKED = 1,
   BADGE_IDLE    = 2
   };

struct SHUDState
   {
   ENUM_HUD_BADGE badge = BADGE_IDLE;
   string badgeText = "IDLE";
   string marketContext = "";
   string activePlan = "";
   string reasoningChain = "";
   string blockReason = "";
   string entriesStatus = "";
   string positionStatus = "";
   };

class CCanvasHUD
   {
   private:
      CCanvas   m_canvas;
      int       m_width;
      int       m_height;
      int       m_fontSize;
      color     m_bgColor;
      color     m_textColor;
      color     m_badgeColor;
      datetime  m_lastRedraw;
      int       m_redrawInterval; // milliseconds

   public:
      void CCanvasHUD(int width = 320, int height = 260, int fontSize = 10,
                      int redrawIntervalMs = 250)
         {
         m_width = width;
         m_height = height;
         m_fontSize = fontSize;
         m_redrawInterval = redrawIntervalMs;
         m_bgColor = C'0x1A1A1A';
         m_textColor = C'0xFFFFFF';
         m_badgeColor = C'0xFFFF00';
         m_lastRedraw = 0;
         }

      bool Create(const string name = "SOV_HUD")
         {
         if(!m_canvas.Create(0, name, 0, 0, m_width, m_height))
            {
            Print("CanvasHUD Create failed: " + IntegerToString(GetLastError()));
            return false;
            }
         m_canvas.Background(m_bgColor);
         m_canvas.Update();
         return true;
         }

      void Destroy()
         {
         m_canvas.Destroy();
         }

      //--- throttle redraws to avoid blocking tick thread
      bool ShouldRedraw()
         {
         datetime now = TimeCurrent();
         if(now - m_lastRedraw >= m_redrawInterval / 1000)
            return true;
         return false;
         }

      void ForceRedraw()
         {
         m_lastRedraw = 0;
         }

      void Render(const SHUDState &state)
         {
         if(!ShouldRedraw()) return;

         m_canvas.Erase(m_bgColor);

         int y = 8;
         int x = 8;
         int lineH = m_fontSize + 4;

         //--- badge
         color badgeCol = (state.badge == BADGE_CLEARED) ? C'0x00FF00' :
                          (state.badge == BADGE_BLOCKED) ? C'0xFF0000' : C'0xFFFF00';
         m_canvas.TextOut(x, y, "[" + state.badgeText + "]", badgeCol, m_fontSize);
         y += lineH;

         //--- separator
         m_canvas.Line(x, y, x + m_width - 16, y, C'0x555555', 1);
         y += lineH;

         //--- market context
         m_canvas.TextOut(x, y, state.marketContext, m_textColor, m_fontSize);
         y += lineH;

         //--- active plan
         m_canvas.TextOut(x, y, state.activePlan, m_textColor, m_fontSize);
         y += lineH;

         //--- reasoning chain (truncated)
         string reasoning = state.reasoningChain;
         if(StringLen(reasoning) > 70)
            reasoning = StringSubstr(reasoning, 0, 67) + "...";
         m_canvas.TextOut(x, y, reasoning, C'0xCCCCCC', m_fontSize - 1);
         y += lineH;

         //--- entries status
         color entriesCol = (state.entriesStatus == "ALLOWED") ? C'0x00FF00' : C'0xFF0000';
         m_canvas.TextOut(x, y, "Entries: " + state.entriesStatus, entriesCol, m_fontSize);
         y += lineH;

         //--- block reason
         if(state.blockReason != "" && state.blockReason != "CLEARED")
            {
            m_canvas.TextOut(x, y, "BLOCK: " + state.blockReason, C'0xFF6600', m_fontSize - 1);
            y += lineH;
            }

         //--- position status
         m_canvas.TextOut(x, y, state.positionStatus, C'0x00FFFF', m_fontSize);
         y += lineH;

         m_canvas.Update();
         m_lastRedraw = TimeCurrent();
         }
   };
