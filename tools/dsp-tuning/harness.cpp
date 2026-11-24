#include "Arduino.h"
uint32_t g_ms = 0;
#include "../../esp32/ledctl/audio.h"
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
int main(int argc, char **argv) {
  std::ifstream f(argv[1]); std::string line; Audio au; uint32_t lastTick = 0; bool first = true;
  while (std::getline(f, line)) {
    if (line.rfind("PLAY", 0) == 0) { printf("PLAY %s\n", line.c_str() + 5); continue; }
    long v[8]; char c; std::stringstream ss(line); int k = 0;
    while (k < 8 && ss >> v[k]) { k++; ss >> c; }
    if (k != 8) continue;
    uint32_t t = v[1];
    if (first) { lastTick = t; first = false; }
    while (lastTick + 10 <= t) { lastTick += 10; g_ms = lastTick; if (au.tick()) printf("BEAT %u host %ld bpm %.1f conf %.1f\n", g_ms, v[0] - (long)(t - g_ms), au.tracker.bpm(), au.tracker.conf); }
    g_ms = t; au.frame(v[3], v[4], v[5], v[6], 5);
  }
}
