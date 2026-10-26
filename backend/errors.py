"""模型不可用时的显式失败。

背景：insightface / pyiqa 在模型缺失时会**自己**去下载；下载失败（无网络、
无代理、被墙）后如果只是 log.warning 然后返回空结果，管线会继续跑完并产出
"所有人脸都没检测到"的照片集——用户看到的是"跑完了"，实际失焦/闭眼判定
全部失效，且没有任何提示。这种静默降级比直接报错难排查得多。

所以模型层缺模型/加载失败一律抛 ModelUnavailable，由 API 层转成可操作的
错误信息（提示用户去下载或配代理），而不是让管线带着坏结果继续。
"""


class ModelUnavailable(RuntimeError):
    """模型缺失或加载失败；message 面向用户，应说明该做什么。"""

    def __init__(self, model: str, detail: str = "", hint: str = ""):
        self.model = model
        self.detail = detail
        self.hint = hint
        msg = f"模型「{model}」不可用"
        if detail:
            msg += f"（{detail}）"
        if hint:
            msg += f"。{hint}"
        super().__init__(msg)

    def to_dict(self) -> dict:
        return {"error": str(self), "model": self.model, "hint": self.hint}
