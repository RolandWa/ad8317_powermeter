import numpy as np, json
import circuit_post as cp
s7p = "results/RF_7port.s7p"
freq, s7, order = cp.read_touchstone(s7p)
df, ds, _ = cp.read_touchstone(cp.DETECTORS["table"]); det = cp.resample(df, ds, freq)
w = 2*np.pi*freq
def rmod(R, L, C):
    z = R + 1j*w*L
    return 1/(1/z + 1j*w*C) if C>0 else z
def cmod(C, esl, esr): return esr + 1j*w*esl + 1/(1j*w*C)
def run(zr1, zr2, zc1, zc2):
    n=len(freq); sl=np.zeros((n,6,6),complex)
    sl[:,0:2,0:2]=det
    for i,z in zip(range(2,6),(zr1,zr2,zc1,zc2)):
        sl[:,i,i] = 1.0 if z is None else cp.gamma(z)
    s11=np.empty(n,complex); a=np.empty((n,6),complex); b=np.empty((n,6),complex)
    for k in range(n):
        b[k]=np.linalg.solve(np.eye(6)-s7[k,1:,1:]@sl[k], s7[k,1:,0]); a[k]=sl[k]@b[k]
        s11[k]=s7[k,0,0]+s7[k,0,1:]@a[k]
    v=np.sqrt(cp.Z0)*(a+b); _,_,_,h=cp.metrics(freq,s11,v)
    return 20*np.log10(abs(s11)), h
base_r = cp.coilcraft_resistor(freq,100.0,"0402DC-0N8")
cap_now = cmod(47e-9,0.7e-9,0.04)
cases = {
 "obecny: 2x100R 0402 (model user), C 0402 ESL0.7": (base_r, base_r, cap_now, cap_now),
 "2x100R thin-film (L0.12n C0.04p), C 0402 ESL0.7": (rmod(100,0.12e-9,0.04e-12),)*2+(cap_now,cap_now),
 "1x50R FC/CH0402 na R1, R2 brak, C 0402": (rmod(50,0.12e-9,0.04e-12), None, cap_now, cap_now),
 "1x50R na R2, R1 brak, C 0402": (None, rmod(50,0.12e-9,0.04e-12), cap_now, cap_now),
 "2x100R thin-film, C 0201 (ESL0.3)": (rmod(100,0.12e-9,0.04e-12),)*2+(cmod(47e-9,0.3e-9,0.05),)*2,
 "1x50R na R1, C 0201 (ESL0.3)": (rmod(50,0.12e-9,0.04e-12), None)+(cmod(47e-9,0.3e-9,0.05),)*2,
 "1x50R na R2, C 0201 (ESL0.3)": (None, rmod(50,0.12e-9,0.04e-12))+(cmod(47e-9,0.3e-9,0.05),)*2,
}
bands=[(0.1e9,1e9),(1e9,3e9),(3e9,6e9),(6e9,10e9)]
out={}
for name,z in cases.items():
    s,h=run(*z); row=[]
    for lo,hi in bands:
        m=(freq>=lo)&(freq<=hi); row.append(round(float(s[m].max()),2))
    m=(freq>=0.1e9)&(freq<=1e9); flat=float(h[m].max()-h[m].min())
    mb=(freq>=0.1e9)&(freq<=10e9)
    out[name]=dict(worst_by_band=row, mean=round(float(s[mb].mean()),2), frac_lt10=round(float((s[mb]<-10).mean()*100),1), H_flat_01_1=round(flat,2))
    print(f"{name:52s} {row} mean {out[name]['mean']} <-10dB {out[name]['frac_lt10']}% Hflat {flat:.2f}")
json.dump(out, open("analysis/component_options.json","w"), indent=1, ensure_ascii=False)
