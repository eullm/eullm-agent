

def test_a_read_github_fails_on_its_side_is_tried_again():
    import httpx

    from editor import sitegit

    answers = iter([httpx.Response(500, json={"message": "Internal Server Error"}),
                    httpx.Response(200, json={"content": "aGVsbG8="})])
    calls, pauses = [], []

    def call(method, path, **kw):
        calls.append(method)
        return next(answers)

    repo = sitegit.Repo(call, "o/r", sleep=pauses.append)
    assert repo.file("llms.txt", "main") == b"hello" and calls == ["GET", "GET"] and pauses == [2]
    # A write is never repeated: it may have gone through.
    once = sitegit.Repo(lambda m, p, **kw: calls.append(m) or httpx.Response(502, text="bad gateway"), "o/r",
                        sleep=pauses.append)
    calls.clear()
    assert once.call("PUT", "/x").status_code == 502 and calls == ["PUT"]
