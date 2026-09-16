import base64, json
import httpx
import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from job_extractor.adapters.moka import MokaAdapter, MokaResponseError

URL="https://app.mokahr.com/campus-recruitment/acme/123"
HTML='<div>{&quot;org&quot;:{&quot;id&quot;:&quot;acme&quot;,&quot;name&quot;:&quot;Acme&quot;},&quot;siteId&quot;:&quot;123&quot;,&quot;aesIv&quot;:&quot;1234567890abcdef&quot;}</div>'
def encrypted(data):
    key=b"abcdef1234567890"; iv=b"1234567890abcdef"
    raw=json.dumps({"code":0,"success":True,"data":data}).encode()
    return {"data":base64.b64encode(AES.new(key,AES.MODE_CBC,iv).encrypt(pad(raw,16))).decode(),"necromancer":key.decode()}
def adapter(total=3, fail_detail=None, page_size=2):
    raws=[{"id":str(i),"title":f"Job {i}"} for i in range(total)]
    def handler(req):
        if req.method=="GET": return httpx.Response(200,text=HTML)
        body=json.loads(req.content)
        if req.url.path.endswith("jobs/v2"):
            batch=raws[body["offset"]:body["offset"]+body["limit"]]
            return httpx.Response(200,json=encrypted({"jobs":batch,"jobStats":{"total":total}}))
        if body["jobId"]==fail_detail: return httpx.Response(500)
        i=body["jobId"]
        return httpx.Response(200,json=encrypted({"id":i,"title":f"Job {i}","hireMode":2,"education":"本科","number":2,"department":{"name":"R&D"},"zhineng":{"name":"Tech"},"locations":[{"cityName":"上海"}],"jobDescription":"<p>岗位职责：</p><p>Build</p><p>岗位要求：</p><p>Learn</p>"}))
    return MokaAdapter(page_size=page_size,client=httpx.Client(transport=httpx.MockTransport(handler)))

def test_match_domains():
    assert MokaAdapter.match(URL); assert MokaAdapter.match("https://x.mokahr.com/a")
def test_reject_other_domain(): assert not MokaAdapter.match("https://example.com")
def test_scope_from_url_and_initialization():
    s=MokaAdapter.parse_scope(URL,HTML); assert (s.org_id,s.site_id,s.site,s.company)==("acme",123,"campus","Acme")
def test_social_scope():
    s=MokaAdapter.parse_scope(URL.replace("campus","social"),HTML); assert s.site=="social"

@pytest.mark.parametrize("prefix", ["campus-recruitment", "campus_apply"])
def test_campus_path_variants_parse_to_same_scope(prefix):
    s=MokaAdapter.parse_scope(f"https://app.mokahr.com/{prefix}/acme/123",HTML)
    assert (s.org_id,s.site_id,s.site,s.company)==("acme",123,"campus","Acme")

def test_malformed_campus_apply_fails_closed():
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope("https://app.mokahr.com/campus_apply/acme/x",HTML)
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope("https://app.mokahr.com/campus_apply/acme/999",HTML)
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope("https://app.mokahr.com/campus_apply/acme",HTML)
def test_bad_scope_url():
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope("https://app.mokahr.com/jobs",HTML)
def test_bad_site_id():
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope(URL.replace("123","x"),HTML)
def test_mismatched_site():
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope(URL.replace("123","999"),HTML)
def test_mismatched_org():
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope(URL.replace("acme","other"),HTML)
def test_missing_iv():
    with pytest.raises(MokaResponseError): MokaAdapter.parse_scope(URL,HTML.replace("aesIv","noIv"))
def test_plain_response(): assert MokaAdapter.decode_response({"code":0,"data":{}},"x")["data"]=={}
def test_encrypted_response(): assert MokaAdapter.decode_response(encrypted({"x":1}),"1234567890abcdef")["data"]["x"]==1
def test_bad_ciphertext():
    with pytest.raises(MokaResponseError): MokaAdapter.decode_response({"data":"x","necromancer":"short"},"1234567890abcdef")
def test_api_error():
    with pytest.raises(MokaResponseError): MokaAdapter.decode_response({"code":2,"msg":"bad"},"x")
def test_list_schema(): assert MokaAdapter._list({"data":{"jobs":[],"jobStats":{"total":0}}})==([],0)
def test_bad_list_schema():
    with pytest.raises(MokaResponseError): MokaAdapter._list({"data":{}})
def test_complete_pagination_and_normalization():
    a=adapter(); r=a.collect(URL)
    assert r.status=="COMPLETE" and (r.total_expected,r.total_fetched,r.total_unique)==(3,3,3)
    assert a.page_count==2 and a.details_attempted==3 and a.detail_strategy=="DETAIL_REQUIRED"
    assert r.jobs[0].locations==["上海"] and r.jobs[0].requirements==["岗位要求：","Learn"]
    assert r.jobs[0].raw_data["list"]["id"]=="0"
def test_duplicate_ids_are_incomplete():
    a=adapter(total=2,page_size=10)
    # Validate uniqueness invariant independently of fetched count via duplicate mock.
    original=a._list
    a._list=lambda p: ([{"id":"1","title":"A"},{"id":"1","title":"A"}],2)
    r=a.collect(URL); assert r.status=="INCOMPLETE" and r.total_unique==1
def test_detail_failure_is_incomplete():
    a=adapter(fail_detail="1"); r=a.collect(URL)
    assert r.status=="INCOMPLETE" and a.details_failed==1 and r.errors
def test_empty_collection_complete():
    r=adapter(total=0).collect(URL); assert r.status=="COMPLETE" and r.total_unique==0
