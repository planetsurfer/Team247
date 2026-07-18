from config import llm_chat, make_runner

reply = llm_chat([{"role": "user", "content": "Reply with exactly: LLM OK"}],
                  temperature=0, purpose="smoke")[:40]
print(f"LLM: OK ({reply})")
r = make_runner()
sb = r.create()
try:
    resp = sb.process.code_run("print(2+2)")
    out = (getattr(resp, "result", "") or "").strip()
    assert "4" in out, f"local exec stdout was {out!r}"
    print(f"LOCAL EXEC: OK (2+2={out} | sandbox {sb.id})")
finally:
    sb.delete()
print("SMOKE OK")
