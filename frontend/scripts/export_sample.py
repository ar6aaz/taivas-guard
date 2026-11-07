"""Build the frontend's sample data from a real IFIC file (exploratory screen, not the pipeline).
usage: export_sample.py space <mdb> <zip> <issue> <yyyy-mm-dd> <stations.xlsx> <out.json>
       export_sample.py terrestrial <mdb> <zip> <issue> <yyyy-mm-dd> <out.json>
Screening only: peak gain, seven Table 21-4 rows, X=0, free space. Results are flags, not concerns."""
import sys,re,json,hashlib,datetime as dt
import numpy as np,pandas as pd
from access_parser import AccessParser
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def tbl(db,t):return pd.DataFrame({k:list(v) for k,v in db.parse_table(t).items()})
def add4m(d):
    y,m,day=map(int,d.split('-'));m+=4
    if m>12:y,m=y+1,m-12
    return f'{y}-{m:02d}-{min(day,28 if m==2 else 30 if m in(4,6,9,11) else 31):02d}'
def num(s):return pd.to_numeric(s,errors='coerce')
Re,Rg=6378.,42164.
LIM=[(1525,1530,-154,.5,4e3,'RR Table 21-4, 1525-1530 MHz'),(2025,2110,-154,.5,4e3,'RR Table 21-4, 2025-2110 MHz'),(2200,2300,-154,.5,4e3,'RR Table 21-4, 2200-2300 MHz'),(2500,2690,-136,.55,1e6,'RR Table 21-4, 2500-2690 MHz'),(8025,8500,-150,.5,4e3,'RR Table 21-4, 8025-8500 MHz'),(17700,19300,-115,.5,1e6,'RR Table 21-4, 17.7-19.3 GHz (X=0)'),(19300,19700,-115,.5,1e6,'RR Table 21-4, 19.3-19.7 GHz')]
lim=lambda P,r,d:P+r*min(max(d-5,0),20)
spread=lambda d:10*np.log10(4*np.pi*(d*1e3)**2)
FI=[(a,b) for a in(59.8,65,70.1) for b in(19.5,25,31.5)]
def geom_gso(ln):
    o=[]
    for la,lo in FI:
        c=np.cos(np.radians(la))*np.cos(np.radians(lo-ln))
        if c<=Re/Rg:continue
        o.append((float(np.sqrt(Re*Re+Rg*Rg-2*Re*Rg*c)),float(np.degrees(np.arctan((c-Re/Rg)/np.sin(np.arccos(c))))),la,lo))
    return o
def geom_ngso(h,inc):
    i=min(inc,180-inc) if inc==inc else 90.;gam=max(59.8-i,0.);r=Re+h
    if np.cos(np.radians(gam))<=Re/r:return []
    mx=90. if gam==0 else float(np.degrees(np.arctan((np.cos(np.radians(gam))-Re/r)/np.sin(np.radians(gam)))))
    o=[]
    for e in sorted({0.,5.,10.,15.,20.,25.,40.,60.,mx}):
        if e<=mx:
            s=np.sin(np.radians(e));o.append((float(np.sqrt((Re*s)**2+2*Re*h+h*h)-Re*s),e,None,None))
    return o
def stations(path):
    raw=pd.read_excel(path,header=None);x=raw.iloc[12:].dropna(how='all').copy();x.columns=raw.iloc[10].astype(str)
    def hz(v):
        m=re.match(r'\s*([\d.,]+)\s*(kHz|MHz|GHz|Hz)',str(v));return float(m[1].replace(',','.'))*{'Hz':1e-6,'kHz':1e-3,'MHz':1,'GHz':1e3}[m[2]] if m else np.nan
    x['rx']=x['RX frequency'].map(hz);x['bw']=x['Channel bandwidth'].map(hz).fillna(0);x=x.dropna(subset=['rx']);x['lo']=x.rx-x.bw/2;x['hi']=x.rx+x.bw/2;return x
def space(a):
    mdb,zp,issue,date,xl,out=a;db=AccessParser(mdb)
    no,cl,ps,sb,gr,em,pv,ob,ge,ng=[tbl(db,t) for t in 'notice com_el pub_ssn s_beam grp emiss provn orbit geo non_geo'.split()]
    names={**dict(zip(ng.ntc_id,ng.sat_name)),**dict(zip(ge.ntc_id,ge.sat_name)),**dict(zip(cl.ntc_id,cl.sat_name))}
    no['ntc_id']=no.ntc_id.astype(int);names={int(k):v for k,v in names.items()};lon=dict(zip(ge.ntc_id.astype(int),num(ge.long_nom)))
    ssn=ps.groupby('ntc_id').ssn_ref.apply(lambda s:sorted(set(map(str,s))));ssn.index=ssn.index.astype(int)
    g=gr.merge(sb[['ntc_id','emi_rcp','beam_name','gain']],on=['ntc_id','emi_rcp','beam_name'],how='left');g['ntc_id']=g.ntc_id.astype(int)
    for c in 'freq_min freq_max gain'.split():g[c]=num(g[c])
    g=g.merge(no[['ntc_id','adm','ntc_type','prov']].rename(columns={'prov':'nprov'}),on='ntc_id')
    tx=g[(g.emi_rcp=='E')&g.ntc_type.isin(['G','N'])].copy();x=stations(xl);lo,hi=x.lo.values,x.hi.values
    tx['nst']=[int(((lo<b)&(hi>a_)).sum()) for a_,b in zip(tx.freq_min,tx.freq_max)]
    e=em.copy();e['ds']=num(e.pwr_ds_max);e['pep']=num(e.pep_max);e=e.dropna(subset=['ds']).drop_duplicates(['grp_id','ds','pep','design_emi'])
    ob=ob.copy();ob['ntc_id']=ob.ntc_id.astype(int);ob['h']=num(ob.perig_km).fillna(num(ob.op_ht_km));ob['inc']=num(ob.inclin_ang);orbs={k:v[['h','inc']].dropna(subset=['h']).drop_duplicates().values.tolist() for k,v in ob.groupby('ntc_id')}
    t=tx.merge(e[['grp_id','ds','pep','design_emi']],on='grp_id').dropna(subset=['gain']);res=[];inband=set()
    gc={}
    for r in t.itertuples():
        for a_,b,P,rr,bw,nm in LIM:
            if not(r.freq_min<b and r.freq_max>a_):continue
            if r.ntc_id not in gc:
                gc[r.ntc_id]=[(d,el,la,lo_,None,None) for d,el,la,lo_ in geom_gso(lon.get(r.ntc_id,np.nan))] if r.ntc_type=='G' else [(d,el,None,None,h,i) for h,i in orbs.get(r.ntc_id,[]) for d,el,_,_ in geom_ngso(h,i)]
            if not gc[r.ntc_id]:continue
            inband.add(r.ntc_id);p=r.ds+10*np.log10(bw)
            if r.pep==r.pep:p=min(p,r.pep)
            for d,el,la,lo_,h,i in gc[r.ntc_id]:
                v=p+r.gain-spread(d);L=lim(P,rr,el);res.append((v-L,r.ntc_id,nm,r.grp_id,r.beam_name,r.freq_min,r.freq_max,r.gain,r.ds,r.pep,r.design_emi,bw,v,L,el,d,la,lo_,h,i,r.nst))
    R=pd.DataFrame(res,columns='margin ntc band grp beam fmin fmax gain ds pep emi bw pfd limit el d la lo h inc nst'.split())
    F=R[R.margin>0];best=F.sort_values('margin',ascending=False).drop_duplicates(['ntc','band'])
    fin=pv[pv.adm=='FIN'].merge(gr[['grp_id','ntc_id','freq_min','freq_max','emi_rcp']],on='grp_id');fin['ntc_id']=fin.ntc_id.astype(int);fin['fmin']=num(fin.freq_min);fin['fmax']=num(fin.freq_max)
    meta=no.set_index('ntc_id');ty=lambda n:'GSO' if meta.ntc_type[n]=='G' else 'NGSO'
    def base(n):return dict(ntcId=int(n),network=names.get(n) or f'Notice {n}',adm=str(meta.adm[n]),orbit=ty(n) if meta.ntc_type[n] in('G','N') else str(meta.ntc_type[n]),provision=str(meta.prov[n]),sections=ssn.get(n,[]),actCode=str(meta.act_code[n]),targetNtcId=(int(meta.tgt_ntc_id[n]) if meta.tgt_ntc_id[n]==meta.tgt_ntc_id[n] and meta.tgt_ntc_id[n] else None))
    fnd=[]
    for r in best.head(60).itertuples():
        ov=x[(x.lo<r.fmax)&(x.hi>r.fmin)]
        fnd.append({**base(r.ntc),'kind':'pfd_screen','band':r.band,'calc':dict(groupId=int(r.grp),beam=r.beam,fMinMhz=r.fmin,fMaxMhz=r.fmax,emission=r.emi,gainDbi=r.gain,powerDensityDbwHz=r.ds,peakPowerDbw=None if r.pep!=r.pep else r.pep,refBandwidthHz=r.bw,distanceKm=round(r.d,1),angleDeg=round(r.el,1),pfd=round(r.pfd,1),limit=round(r.limit,1),marginDb=round(r.margin,1),altitudeKm=None if r.h!=r.h or r.h is None else r.h,inclinationDeg=None if r.inc!=r.inc or r.inc is None else r.inc,lat=None if r.la!=r.la else r.la,lon=None if r.lo!=r.lo else r.lo),'stationCount':int(r.nst),'stations':[dict(id=int(s['Station number']),rxMhz=s.rx,licence=None if pd.isna(s['Class of licence']) else s['Class of licence'],lat=float(s.Latitude),lon=float(s.Longitude)) for _,s in ov.head(6).iterrows()]})
    br=[]
    for n,d in fin.groupby('ntc_id'):
        br.append({**base(n),'kind':'br_listed','band':f'{d.fmin.min():g}-{d.fmax.max():g} MHz','coordProvisions':sorted(set(map(str,d.coord_prov))),'agreeStatus':sorted(set(map(str,d.agree_st))),'directions':sorted(set(map(str,d.emi_rcp))),'groupCount':int(d.grp_id.nunique()),'stationCount':int(((x.lo<d.fmax.max())&(x.hi>d.fmin.min())).sum())})
    txn=set(tx.ntc_id);ovn=set(tx[tx.nst>0].ntc_id);fln=set(F.ntc)
    res=dict(issue=int(issue),date=date,domain='space',deadline=add4m(date),file=dict(zip=zp.split('/')[-1],zipSha256=sha(zp),mdb=mdb.split('/')[-1],mdbSha256=sha(mdb)),
      counts=dict(notices=len(no),byType=no.ntc_type.value_counts().to_dict(),sections=ps.ssn_ref.value_counts().to_dict(),groups=len(gr),emissions=len(em),brFinlandNetworks=len({names.get(n,n) for n in fin.ntc_id})),
      funnel=[dict(key='notices',label='Filings in the issue',n=len(no)),dict(key='tx',label='Satellite transmits',n=len(txn)),dict(key='overlap',label='Overlaps a Finnish receiver',n=len(ovn)),dict(key='limit',label='In a limit band we check, can reach Finland',n=len(inband)),dict(key='flagged',label='Above a limit at peak gain',n=len(fln)),dict(key='flaggedOverlap',label='Flagged and overlaps a Finnish receiver',n=len(fln&ovn))],
      findings=fnd,brListed=br,findingsTotal=int(best.shape[0]))
    json.dump(res,open(out,'w'),indent=0,default=lambda o:o.item() if hasattr(o,'item') else str(o));print(issue,res['funnel'],len(fnd),len(br))
def terr(a):
    mdb,zp,issue,date,out=a;db=AccessParser(mdb);t,an,ge=[tbl(db,n) for n in 'fxm_terra fxm_ant fxm_geo'.split()]
    B=np.array([[60.0,27.8],[61.0,28.9],[62.0,30.9],[63.5,31.5],[65.0,29.8],[67.0,29.5],[68.8,28.5],[69.8,29.0],[70.05,27.5],[69.0,20.5],[66.0,23.7],[60.0,20.0],[59.9,23.0],[59.9,25.5]])
    def dist(la,lo):
        p=np.radians(la);q=np.radians(B[:,0]);return float((6371*np.arccos(np.clip(np.sin(p)*np.sin(q)+np.cos(p)*np.cos(q)*np.cos(np.radians(lo-B[:,1])),-1,1))).min())
    lm=t[t.stn_cls.isin(['FB','ML','FP','FL'])&t.adm.ne('FIN')].merge(ge[['geo_key','long_dec','lat_dec','site_name']],on='geo_key',how='left')
    lm['la']=num(lm.lat_dec);lm['lo']=num(lm.long_dec);lm=lm.dropna(subset=['la','lo']);lm['dkm']=[dist(p,q) for p,q in zip(lm.la,lm.lo)];near=lm[lm.dkm<=300].sort_values('dkm')
    a0=an.assign(pwr=num(an.pwr_dbw),g=num(an.gain_max),ha=num(an.hgt_agl)).sort_values('pwr',ascending=False).drop_duplicates('terrakey').set_index('terrakey')
    nr=[]
    for r in near.itertuples():
        A=a0.loc[r.terrakey] if r.terrakey in a0.index else None;f=lambda c:None if A is None or A[c]!=A[c] else float(A[c])
        nr.append(dict(terrakey=int(r.terrakey),adm=r.adm,stationClass=r.stn_cls,fMhz=float(r.freq_assgn),bandwidthKhz=None if pd.isna(r.bdwdth) else float(r.bdwdth),intent=str(r.intent),noticeType=str(r.notice_typ),site=str(r.site_name),lat=r.la,lon=r.lo,borderKm=round(r.dkm,1),powerDbw=f('pwr'),gainDbi=f('g'),heightM=f('ha'),powerType=None if A is None else str(A.pwr_eiv)))
    res=dict(issue=int(issue),date=date,domain='terrestrial',file=dict(zip=zp.split('/')[-1],zipSha256=sha(zp),mdb=mdb.split('/')[-1],mdbSha256=sha(mdb)),counts=dict(assignments=len(t),byAdm=t.adm.value_counts().to_dict(),byIntent=t.intent.value_counts().to_dict(),landMobileForeign=len(lm),within100=int((lm.dkm<=100).sum()),within300=len(near)),nearBorder=nr)
    json.dump(res,open(out,'w'),indent=0,default=lambda o:o.item() if hasattr(o,'item') else str(o));print(issue,res['counts'])
(space if sys.argv[1]=='space' else terr)(sys.argv[2:])
