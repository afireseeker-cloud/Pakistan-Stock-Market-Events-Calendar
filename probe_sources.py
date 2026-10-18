import requests, re, sys
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
W="https://pkdata.afireseeker.workers.dev"
form={"type":"C","symbol":"","query":"","count":"50","offset":"0","date_from":"2026-10-10","date_to":"2026-10-18","page":"annc"}
def show(name, r):
    t=r.text if r is not None else ''
    print(f"== {name}: {r.status_code} {r.headers.get('content-type')} len={len(r.content)} tr={t.count('<tr')}")
    print(re.sub(r'\s+',' ',t[:400]))
def go(name, method, url, **kw):
    try:
        r=requests.request(method,url,headers={**UA,**kw.pop('headers',{})},timeout=40,**kw); show(name,r)
    except Exception as e: print("==",name,"ERR",e)
go("dps direct POST annc","POST","https://dps.psx.com.pk/announcements",data=form,headers={"X-Requested-With":"XMLHttpRequest","Referer":"https://dps.psx.com.pk/announcements/companies"})
from urllib.parse import urlencode
go("worker GET annc","GET",W+"/announcements?"+urlencode(form))
go("worker GET payouts","GET",W+"/payouts")
go("worker GET company HUBC","GET",W+"/company/HUBC")
for u in ["https://easydata.sbp.org.pk/api/v1/series/TS_GP_BOP_WR_M.WR0340/data?format=json",
          "https://easydata.sbp.org.pk/api/v1/series/TS_GP_BOP_WR_M.WR0340/data?format=csv",
          "https://easydata.sbp.org.pk/api/v1/datasets",
          "https://easydata.sbp.org.pk/apex/f?p=10:1",
          "https://www.sbp.org.pk/",
          "https://www.sbp.org.pk/ecodata/Homeremit.xls",
          "https://www.sbp.org.pk/ecodata/Homeremit.xlsx",
          "https://www.sbp.org.pk/ecodata/Balancepayment_BPM6.xls",
          "https://www.sbp.org.pk/ecodata/Balancepayment_BPM6.pdf",
          "https://www.sbp.org.pk/ecodata/index2.asp",
          "https://www.sbp.org.pk/m_policy/index.asp",
          "https://www.pbs.gov.pk/cpi",
          "https://www.pbs.gov.pk/cpi-latest",
          "https://www.pbs.gov.pk/press-releases",
          "https://www.pbs.gov.pk/"]:
    go(u,"GET",u)
