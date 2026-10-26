// Real firmware protocol on a host clock, not an alternative implementation.
// Lines: @<milliseconds> to set clock, !0/!1 for released/pressed button,
// otherwise one serial line. Every line emits PINS,<mask>,<phase> for assertions.
#include <iostream>
#include <string>
#include "arduino/AdaptiveTrafficArduino/TrafficProtocol.h"
void output(const char* line) { std::cout << line << '\n'; }
int main() {
  traffic::Protocol p(output); uint32_t now=0; bool button=false; p.begin(now);
  std::string line;
  while (std::getline(std::cin,line)) {
    if (line.empty()) { p.byte('\n',now); }
    else if (line[0]=='@') now=uint32_t(std::stoul(line.substr(1)));
    else if (line[0]=='!') button=line=="!1";
    else if (line[0]=='%') { for (char c:line.substr(1)) p.byte(c,now); }
    else { for (char c:line) p.byte(c,now); p.byte('\n',now); }
    p.poll(now,button);
    std::cout << "PINS," << traffic::lamps(p.phase) << ',' << traffic::name(p.phase) << std::endl;
  }
}
