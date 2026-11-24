import numpy as np, sim, sys
from tracker import BeatTracker
def track(a, fluxw=(1.0,0.8,0.6), lead=0, **kw):
    kf=[sim.NoiseKF() for _ in range(4)]; prev=[0,0,0]; bt=BeatTracker(lead=lead, **kw)
    out=[]; t0=a[0][1]; cur=None; acc=0
    for r in a:
        for k,z in zip(kf,(r[3],r[4],r[5],r[6])): k.update(z)
        s=[kf[1].snr,kf[2].snr,kf[3].snr]
        o=sum(w*max(0,si-pi) for w,si,pi in zip(fluxw,s,prev)); prev=s
        b=(r[1]-t0)//10
        if cur is None: cur=b
        while cur<b:
            fired=bt.push(acc); out.append((t0+cur*10, fired, bt.L, bt.conf)); acc=0; cur+=1
        acc=max(acc,o)
    return out
def score(a, play, out, grid):
    import bisect
    hm=[(r[1],r[0]) for r in a]; es=[h[0] for h in hm]
    def host(t):
        i=min(bisect.bisect_left(es,t),len(es)-1); return hm[i][1]+(t-hm[i][0])
    bts=np.array([(host(t)-play)/1000 for t,f,_,_ in out if f])
    best=None
    for off in np.arange(0,0.45,0.005):
        d=bts-off; on=sum(np.min(np.abs(grid-v))<0.07 for v in d if grid[0]<=v<grid[-1]+0.5)
        if best is None or on>best[0]: best=(on,off)
    on,off=best; d=bts-off; inm=[v for v in d if grid[0]<=v<grid[-1]+0.5]
    errs=np.array([v-grid[np.argmin(np.abs(grid-v))] for v in inm])
    return on,len(inm),off,np.std(errs)*1000, 6000/np.median([x[2] for x in out[-500:]])
if __name__=="__main__":
    a,play=sim.load('caps/testtrack.csv'); grid=np.arange(2.0,34.0,0.5)
    for kw in [dict(gain=0.5,nper=4),dict(gain=0.2,nper=4),dict(gain=0.2,nper=7),dict(gain=0.15,nper=7,smooth=1),dict(gain=0.1,nper=7,smooth=2),dict(gain=0.25,nper=7,smooth=1)]:
        out=track(a,(1,1,1),**kw); on,n,off,sd,bpm=score(a,play,out,grid)
        print(kw, f'on-grid {on}/{n} off {off*1000:.0f}ms jitter {sd:.0f}ms bpm {bpm:.1f}')
