import requests, re
W="https://pkdata.afireseeker.workers.dev"
f="type=C&symbol=&query=&count=50&offset=0&date_from=2026-10-05&date_to=2026-10-18&page=annc"
def show(n,r):
    print("==",n,r.status_code,dict(r.headers)); t=r.text; print("len",len(t),"tr",t.count("<tr")); print(re.sub(r"\s+"," ",t[:1500])); 
    i=t.find("tbl__body"); print("tbl__body at",i, re.sub(r"\s+"," ",t[i-200:i+800]) if i>0 else "")
show("relay POST", requests.post(W+"/announcements", data=f, headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=60))
show("relay GET qs", requests.get(W+"/announcements?"+f, timeout=60))
show("relay payouts", requests.post(W+"/payouts", data="symbol=&count=50&offset=0", headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=60))
show("relay GET companies page", requests.get(W+"/announcements/companies", timeout=60))
