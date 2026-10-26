#pragma once
// The same allocation-free implementation is compiled for AVR and host tests.
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

namespace traffic {
enum Phase { SAFE, NS_GREEN, NS_YELLOW, ALL_RED_TO_EW, EW_GREEN, EW_YELLOW,
             ALL_RED_TO_NS, ALL_RED_TO_PED, PED_GREEN, PED_CLEARANCE, ALL_RED_AFTER_PED };
inline const char* name(Phase p) {
  switch (p) {
    case NS_GREEN: return "NS_GREEN"; case NS_YELLOW: return "NS_YELLOW";
    case ALL_RED_TO_EW: return "ALL_RED_TO_EW"; case EW_GREEN: return "EW_GREEN";
    case EW_YELLOW: return "EW_YELLOW"; case ALL_RED_TO_NS: return "ALL_RED_TO_NS";
    case ALL_RED_TO_PED: return "ALL_RED_TO_PED"; case PED_GREEN: return "PED_GREEN";
    case PED_CLEARANCE: return "PED_CLEARANCE"; case ALL_RED_AFTER_PED: return "ALL_RED_AFTER_PED";
    default: return "SAFE_ALL_RED";
  }
}
// R,Y,G for N,S,E,W, then pedestrian R,G. Exactly five lamps are on.
inline uint16_t lamps(Phase p) {
  uint16_t bits = 0;
  for (uint8_t i=0; i<4; ++i) {
    uint8_t color = ((i<2 && p==NS_GREEN) || (i>=2 && p==EW_GREEN)) ? 2 :
                    ((i<2 && p==NS_YELLOW) || (i>=2 && p==EW_YELLOW)) ? 1 : 0;
    bits |= uint16_t(1) << (3*i+color);
  }
  return bits | (uint16_t(1) << (p==PED_GREEN ? 13 : 12));
}

class Protocol {
public:
  static const uint32_t WATCHDOG_MS = 2500;
  static const uint32_t RESYNC_MS = 4000; // yellow 3000 + all-red 1000
  typedef void (*Output)(const char*);
  explicit Protocol(Output output): output_(output) {}
  Phase phase = SAFE;

  void begin(uint32_t now, bool buttonLow=false) {
    phase=SAFE; entered_=lastMessage_=now; ready_=armed_=false;
    sequence_=0; hasTick_=false; used_=0; discard_=false; cached_[0]=0;
    raw_=stable_=buttonLow; buttonSince_=now;
  }
  void poll(uint32_t now, bool buttonLow) {
    if (ready_ && uint32_t(now-lastMessage_) >= WATCHDOG_MS) fault("TIMEOUT", now);
    if (used_ && uint32_t(now-lastByte_) >= 500) {
      used_=0; discard_=true; fault("PARTIAL_LINE", now);
    }
    if (buttonLow != raw_) { raw_=buttonLow; buttonSince_=now; }
    if (raw_ != stable_ && uint32_t(now-buttonSince_) >= 40) {
      stable_=raw_;
      if (stable_ && ready_) output_("BUTTON,PED"); // no GPIO changes
    }
  }
  void byte(char c, uint32_t now) {
    poll(now, raw_);
    if (c=='\n') {
      if (!discard_ && used_) { input_[used_]=0; command(input_, now); }
      used_=0; discard_=false; return;
    }
    if (discard_) return;
    if (c=='\r') return;
    if (c<32 || c>126 || used_>=sizeof(input_)-1) {
      used_=0; discard_=true; fault("INVALID_LINE",now); return;
    }
    input_[used_++]=c; lastByte_=now;
  }

private:
  Output output_;
  uint32_t entered_=0,lastMessage_=0,lastByte_=0,sequence_=0,tick_=0,buttonSince_=0;
  bool ready_=false,armed_=false,hasTick_=false,discard_=false,raw_=false,stable_=false;
  char input_[96]={0}, cached_[96]={0}, reply_[64]={0};
  uint8_t used_=0;
  void safe(uint32_t now) { phase=SAFE; entered_=now; armed_=false; hasTick_=false; }
  void fault(const char* reason,uint32_t now) {
    safe(now); ready_=false; cached_[0]=0;
    char response[64]; snprintf(response,sizeof(response),"ERROR,%s",reason); output_(response);
  }
  bool number(const char* s,uint32_t& result) {
    if (!s || !*s) return false;
    result=0;
    for (;*s;++s) {
      if (*s<'0'||*s>'9'||result>429496729UL||(result==429496729UL && *s>'5')) return false;
      result=result*10+(*s-'0');
    }
    return true;
  }
  bool allowed(Phase next,uint32_t now) {
    uint32_t elapsed=now-entered_;
    if (phase==SAFE) return armed_ && elapsed>=RESYNC_MS && next==NS_GREEN;
    if (next==phase) return true;
    switch (phase) {
      case NS_GREEN: return next==NS_YELLOW;
      case EW_GREEN: return next==EW_YELLOW;
      case NS_YELLOW: return elapsed>=3000 && (next==ALL_RED_TO_EW||next==ALL_RED_TO_PED);
      case EW_YELLOW: return elapsed>=3000 && (next==ALL_RED_TO_NS||next==ALL_RED_TO_PED);
      // A pedestrian can replace an existing all-red destination without resetting its timer.
      case ALL_RED_TO_NS: return next==ALL_RED_TO_PED||(elapsed>=1000&&next==NS_GREEN);
      case ALL_RED_TO_EW: return next==ALL_RED_TO_PED||(elapsed>=1000&&next==EW_GREEN);
      case ALL_RED_TO_PED: return elapsed>=1000&&next==PED_GREEN;
      case PED_GREEN: return elapsed>=10000&&next==PED_CLEARANCE;
      case PED_CLEARANCE: return elapsed>=3000&&next==ALL_RED_AFTER_PED;
      case ALL_RED_AFTER_PED: return elapsed>=1000&&(next==NS_GREEN||next==EW_GREEN);
      default: return false;
    }
  }
  void command(const char* line,uint32_t now) {
    if (!strcmp(line,"HELLO")) {
      safe(now); ready_=true; sequence_=0; cached_[0]=0; lastMessage_=now;
      output_("READY,ADAPTIVETRAFFIC,1,3000,1000,10000,3000"); return;
    }
    if (!ready_) { fault("HELLO_REQUIRED",now); return; }
    char copy[96]; strcpy(copy,line);
    char* parts[5]; uint8_t count=1; parts[0]=copy;
    for (char* p=copy;*p;++p) if (*p==',') {
      if (count>=5) { fault("FORMAT",now); return; }
      *p=0; parts[count++]=p+1;
    }
    uint32_t seq;
    if (count<2||!number(parts[1],seq)||seq==0) { fault("SEQUENCE",now); return; }
    if (seq==sequence_ && !strcmp(line,cached_)) {
      lastMessage_=now; output_(reply_); return; // no repeated state/timer change
    }
    if (seq!=sequence_+1 || sequence_==UINT32_MAX) { fault("SEQUENCE",now); return; }
    if (!strcmp(parts[0],"STOP") && count==2) safe(now);
    else if (!strcmp(parts[0],"SYNC") && count==2) { safe(now); armed_=true; }
    else if (!strcmp(parts[0],"PING") && count==2) { /* watchdog only */ }
    else if (!strcmp(parts[0],"STATE") && count==4) {
      uint32_t tick;
      if (!number(parts[2],tick)||(!hasTick_&&tick!=0)||
          (hasTick_&&(tick<tick_||tick-tick_>1000))) { fault("MODEL_TIME",now); return; }
      Phase next=SAFE;
      for (int i=1;i<=ALL_RED_AFTER_PED;++i) if (!strcmp(parts[3],name(Phase(i)))) next=Phase(i);
      if (next==SAFE||!allowed(next,now)) { fault("TRANSITION",now); return; }
      bool retarget=(phase==ALL_RED_TO_NS||phase==ALL_RED_TO_EW)&&next==ALL_RED_TO_PED;
      if (next!=phase&&!retarget) entered_=now;
      phase=next; tick_=tick; hasTick_=true;
    } else { fault("COMMAND",now); return; }
    sequence_=seq; lastMessage_=now; strcpy(cached_,line);
    snprintf(reply_,sizeof(reply_),"ACK,%lu,%s",(unsigned long)seq,name(phase));
    output_(reply_);
  }
};
} // namespace traffic
