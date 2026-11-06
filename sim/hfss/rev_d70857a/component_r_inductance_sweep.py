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
cap_now = cmod(47e-9,0.7e-9,0.04)
for L in [0.0,0.12,0.4,0.8,1.2,1.6,2.0]:
    for tag,z in [("2x100R",(rmod(100,L*1e-9,0.04e-12),)*2),("50R@R2",(None,rmod(50,L*1e-9,0.04e-12)))]:
        s,h=run(*z,cap_now,cap_now); mb=(freq>=0.1e9)&(freq<=10e9)
        r=[round(float(s[(freq>=a)&(freq<=b)].max()),2) for a,b in [(0.1e9,1e9),(1e9,3e9),(3e9,6e9),(6e9,10e9)]]
        print(f"L={L:4.2f}nH {tag:7s} {r} mean {s[mb].mean():.2f}")
