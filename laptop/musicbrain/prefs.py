"""Your taste, learned from A/B picks on the bench. Stored per kind of music (genre family).

  palette scores : which colour sets you pick for R&B, hip-hop, ...
  look swaps     : "for R&B, when the director wants look X, you prefer Y"
  knobs          : multipliers on brightness (exposure), white, voice light, pulses, smoothness
"""
import json
import os
import threading

KNOBS = {"exposure": (0.5, 2.0), "white": (0.2, 4.0), "vocal": (0.3, 2.5), "pulse": (0.4, 1.6), "release": (0.3, 4.0)}


class Prefs:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.d = {"palette": {}, "look": {}, "knob": {}, "votes": 0}
        if os.path.exists(path):
            try:
                self.d.update(json.load(open(path)))
            except Exception:
                pass

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            json.dump(self.d, open(tmp, "w"), indent=1)
            os.replace(tmp, self.path)

    @staticmethod
    def fam(f):
        return f or "unknown"

    # ---- reading
    def pal_scores(self, fam):
        return self.d["palette"].get(self.fam(fam), {})

    def look_sub(self, fam, look):
        alts = self.d["look"].get(self.fam(fam), {}).get(look, {})
        if not alts:
            return None
        best = max(alts, key=alts.get)
        return best if alts[best] >= 1.5 else None

    def knob(self, fam, k):
        return self.d["knob"].get(self.fam(fam), {}).get(k, 1.0)

    # ---- learning
    def vote(self, q, winner):
        """q: {"type", "fam", "key", "a", "b"}; winner: 1 (A, current), 2 (B, the alternative), 0 (neither)"""
        f = self.fam(q["fam"])
        with self.lock:
            self.d["votes"] += 1
            if q["type"] == "palette":
                sc = self.d["palette"].setdefault(f, {})
                a, b = q["a"], q["b"]
                if winner == 1:
                    sc[a] = sc.get(a, 0) + 1; sc[b] = sc.get(b, 0) - 1
                elif winner == 2:
                    sc[b] = sc.get(b, 0) + 1; sc[a] = sc.get(a, 0) - 1
                else:
                    sc[a] = sc.get(a, 0) - 0.5; sc[b] = sc.get(b, 0) - 0.5
            elif q["type"] == "look":
                alts = self.d["look"].setdefault(f, {}).setdefault(q["key"], {})
                b = q["b"]
                alts[b] = alts.get(b, 0) + (1.5 if winner == 2 else -1 if winner == 1 else -0.5)
            elif q["type"] == "knob":
                k = q["key"]
                kn = self.d["knob"].setdefault(f, {})
                cur, alt = q["a"], q["b"]
                lo, hi = KNOBS[k]
                if winner == 2:
                    new = cur + 0.6 * (alt - cur)
                elif winner == 1:
                    new = cur - 0.15 * (alt - cur)
                else:
                    new = cur
                kn[k] = round(min(hi, max(lo, new)), 3)
        self.save()
