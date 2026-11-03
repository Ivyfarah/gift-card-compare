#!/usr/bin/env python3
"""Temporary diagnostic for public retail category page HTML structure. Not a price source."""
from __future__ import annotations
import re
import urllib.request
URLS={
 "costco": "https://www.costco.com/CategoryDisplay?catalogId=10701&identifier=gift-cards-tickets&storeId=10301",
 "samsclub": "https://www.samsclub.com/browse/extra-savings-gift-cards/7050204",
 "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?browsedCategory=pcmcat1514907515612&id=pcat17071&qp=percentdiscount_facet%3DDiscount~20%25+Off+or+More&st=categoryid%24pcmcat1514907515612",
}
for name,url in URLS.items():
 try:
  req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; GiftCardCompare/1.0; public retail price check)"})
  with urllib.request.urlopen(req,timeout=18) as r:
   page=r.read(1800000).decode("utf-8","replace")
  print(name,"status=ok","bytes",len(page),"ldjson",len(re.findall('application/ld\\+json',page,re.I)),
        "name-hit",len(re.findall("Cinemark|Regal Cinemas|Fogo de Chao|inKind",page,re.I)),
        "price-hit",len(re.findall("39\\.99|37\\.50|160\\.00|79\\.99",page,re.I)))
  for pattern in ["Cinemark","Regal Cinemas","inKind"]:
   m=re.search(pattern,page,re.I)
   if m:
    sample=re.sub(r"\\s+"," ",page[max(0,m.start()-150):m.start()+300])
    print(name,"sample",pattern, sample[:450])
 except Exception as e:print(name,"unavailable",type(e).__name__,str(e)[:160])
