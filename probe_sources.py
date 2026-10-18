import requests, re, io, os
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
os.makedirs("probe", exist_ok=True)
def get(u):
    r=requests.get(u,headers=UA,timeout=60); print("==",u,r.status_code,r.headers.get('content-type'),len(r.content)); return r
r=get("https://www.sbp.org.pk/ecodata/Balancepayment_BPM6.xls")
open("probe/bpm6.xls","wb").write(r.content)
import xlrd
b=xlrd.open_workbook(file_contents=r.content)
for sh in b.sheets():
    print("SHEET",sh.name,sh.nrows,sh.ncols)
    for i in range(min(sh.nrows,60)):
        row=[str(c.value)[:14] for c in sh.row(i)]
        if any(x.strip() for x in row): print(i,"|".join(row[:16]))
for u in ["https://www.pbs.gov.pk/wp-content/uploads/2020/07/Press-Release-September-2026.pdf",
          "https://www.sbp.org.pk/assets/document/publications/MPS-July-2026-Eng.pdf",
          "https://www.sbp.org.pk/m_policy/2025/MPS-Dec-2025-Eng.pdf",
          "https://www.sbp.org.pk/press/2026/Pr-09-Feb-2026.pdf"]:
    r=get(u)
    if r.status_code==200 and r.content[:4]==b'%PDF':
        import pdfplumber
        with pdfplumber.open(io.BytesIO(r.content)) as p:
            t="\n".join((pg.extract_text() or '') for pg in p.pages[:2])
        print(re.sub(r'[ \t]+',' ',t[:1800]))
r=get("https://www.pbs.gov.pk/price-statistics/")
open("probe/pbs_price.html","w").write(r.text)
print(sorted(set(re.findall(r'https://www\.pbs\.gov\.pk/wp-content/uploads/[^"\']+\.pdf', r.text)))[:40])
r=get("https://pkdata.afireseeker.workers.dev/company/HUBC"); open("probe/hubc.html","w").write(r.text)
