"""A conflict label needs two sources behind it (graph conflict or two cited source lines)."""
from web.app import answer_status

EV = {"e": {}}


def test_model_conflict_without_two_sources_is_downgraded():
    ans = "Status: conflict\n## Answer\nNo.\n## Conflicts / Superseded\nOnly one side [a → corpus/docs/x.md:3] [b → corpus/docs/x.md:3]"
    assert answer_status(ans, [], EV) == ("applicable", "conflict_unsupported")


def test_conflict_with_two_cited_lines_stands():
    ans = ("Status: conflict\n## Conflicts / Superseded\nchat says 4h [a → corpus/chats/c.md:18]\n"
           "he replies no [b → corpus/chats/c.md:21]\n## Gaps\nnone")
    assert answer_status(ans, [], EV) == ("conflict", "answer_label")


def test_graph_conflict_alone_is_enough():
    ans = "Status: conflict\n## Answer\nx [a → corpus/docs/x.md:3]"
    assert answer_status(ans, [{"conflicts": [{"subject": "s"}]}], EV) == ("conflict", "answer_label")
