from job_extractor.discovery.dynamic import wait_for_hydration


class _Locator:
    def __init__(self, page, selector): self.page,self.selector=page,selector
    def count(self): return self.page.signal()["links"] if self.selector == "a[href]" else 0
    def inner_text(self): return "x" * self.page.signal()["textLength"]


class _Page:
    def __init__(self, signals): self.signals=signals;self.index=0
    def signal(self): return self.signals[min(self.index,len(self.signals)-1)]
    def evaluate(self, _script):
        s=self.signal();return {"readyState":s["readyState"],"rootChildren":s["rootChildren"],"textLength":s["textLength"]}
    def locator(self, selector): return _Locator(self,selector)
    def wait_for_timeout(self, _ms): self.index+=1


def _signal(ready="interactive", root=0, text=0, links=0):
    return {"readyState":ready,"rootChildren":root,"textLength":text,"links":links}


def test_interactive_mounted_app_is_hydrated_without_load():
    assert wait_for_hydration(_Page([_signal(root=1)]),lambda:0)["stabilized"] is True


def test_interactive_meaningful_body_is_hydrated():
    assert wait_for_hydration(_Page([_signal(text=100)]),lambda:0)["stabilized"] is True


def test_interactive_empty_app_stays_unhydrated():
    assert wait_for_hydration(_Page([_signal()]),lambda:0,timeout_ms=500,interval_ms=250)["stabilized"] is False


def test_interactive_shell_only_stays_unhydrated():
    assert wait_for_hydration(_Page([_signal(text=12)]),lambda:0,timeout_ms=500,interval_ms=250)["stabilized"] is False


def test_complete_normal_content_is_unchanged():
    assert wait_for_hydration(_Page([_signal(ready="complete",text=100)]),lambda:0)["stabilized"] is True


def test_pending_resource_does_not_block_mounted_interactive_app():
    assert wait_for_hydration(_Page([_signal(ready="interactive",root=1)]),lambda:0)["stabilized"] is True
