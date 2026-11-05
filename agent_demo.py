import time
import json

TOOLS = {
    "search_kb":    {"desc": "查校园规则知识库", "params": ["query"], "write": False},
    "query_borrow": {"desc": "按学号查当前借阅记录", "params": ["student_id"], "write": False},
    "calc_fine":    {"desc": "按逾期天数和册数算罚金", "params": ["days", "books"], "write": False},
    "update_record": {"desc": "修改借阅记录（写操作）", "params": ["student_id"], "write": True},
}

KB = {
    "罚金": "逾期按每册每天 0.1 元计，单册上限 20 元；罚金未结清不能借新书",
    "借期": "普通图书借期 30 天，可续借一次 15 天",
    "额度": "学生最多同时借 8 册",
    "取件": "取件需取件码；包裹免费存放 72 小时",
    "缓存": "热门查询走缓存，有效期 5 分钟",
}
BORROW_DB = {"2024001": ["数据结构", "计算机网络"], "2024002": ["操作系统"]}


# ---------------------------------------------------------------
# 2. 工具执行 —— 关键：执行权在应用侧，不在模型
#    面试点：模型只返回「我想调什么」，参数一律不可信，必须校验后再执行
# ---------------------------------------------------------------
def execute(name, args, confirmed=False):
    # ① 工具白名单：模型编出一个不存在的工具名，直接拒绝
    if name not in TOOLS:
        return {"error": "未注册的工具，拒绝执行"}
    spec = TOOLS[name]

    # ② 参数校验：缺参数 / 类型不对，都不许执行
    for p in spec["params"]:
        if p not in args:
            return {"error": "缺少参数 " + p}

    # ③ 写操作要人点头：读自动执行，写必须人工确认
    if spec["write"] and not confirmed:
        return {"error": "写操作需人工确认，已拦截"}

    # ④ 业务层校验
    if name == "search_kb":
        q = str(args["query"])
        hits = [v for k, v in KB.items() if k in q]
        return {"found": hits or ["知识库中未找到该主题"]}

    if name == "query_borrow":
        sid = str(args["student_id"])
        books = BORROW_DB.get(sid, [])
        return {"student_id": sid, "books": books, "count": len(books)}

    if name == "calc_fine":
        try:
            days, books = int(args["days"]), int(args["books"])
        except (TypeError, ValueError):
            return {"error": "days / books 必须是整数"}
        if days < 0 or books < 0:
            return {"error": "天数与册数不能为负"}
        raw = days * books * 0.1
        cap = 20 * books                       # 单册上限 20 元
        return {"fine": round(min(raw, cap), 2), "capped": raw > cap}

    if name == "update_record":
        return {"ok": True, "note": "已修改（经人工确认）"}

    return {"error": "工具未实现"}


# ---------------------------------------------------------------
# 3. 「模型决策」—— 这里用规则模拟，只为了不依赖 API key
#    返回两种结果：tool_call（想调工具） / finish（可以回答了）
# ---------------------------------------------------------------
def decide(question, history):
    called = [h["name"] for h in history]
    if "search_kb" not in called:
        kw = "罚金" if ("罚金" in question or "逾期" in question) else "借期"
        return {"type": "tool_call", "thought": "问题涉及规则，先查知识库",
                "name": "search_kb", "args": {"query": kw}}
    if ("罚金" in question or "逾期" in question) and "calc_fine" not in called:
        return {"type": "tool_call", "thought": "拿到规则了，代入天数算一下",
                "name": "calc_fine", "args": {"days": 12, "books": 1}}
    obs = "；".join(str(h["obs"]) for h in history)
    return {"type": "finish", "thought": "信息够了，可以作答",
            "answer": "根据查到的规则与计算结果：" + obs}


def decide_stuck(question, history):
    """故意一直重复同一个调用，用来演示查重闸"""
    return {"type": "tool_call", "thought": "再查一次试试",
            "name": "search_kb", "args": {"query": "罚金"}}


# ---------------------------------------------------------------
# 4. Agent 主循环 + 五道闸
# ---------------------------------------------------------------
MAX_STEPS = 6          # 闸1：最大步数
TIME_LIMIT = 3.0       # 闸2：总超时（秒）
TOKEN_BUDGET = 800     # 闸4：token 预算（这里用字符数粗估）
                       # 闸3：重复调用检测（相同工具 + 相同参数）
                       # 闸5：显式 finish 工具，只有它算正常结束


def run(question, decide_fn=decide, label=""):
    print("\n" + "=" * 60)
    print("CASE {}：{}".format(label, question))
    print("=" * 60)
    history, seen, used = [], set(), 0
    start = time.monotonic()

    for step in range(1, MAX_STEPS + 1):
        # 闸2：超时
        if time.monotonic() - start > TIME_LIMIT:
            print("  [闸2 超时] 超过 {:.0f}s，强制结束".format(TIME_LIMIT))
            break
        # 闸4：预算
        if used > TOKEN_BUDGET:
            print("  [闸4 预算] 超过 {} 字符，强制结束".format(TOKEN_BUDGET))
            break

        act = decide_fn(question, history)
        thought = act.get("thought", "")
        used += len(thought) + 30

        # 闸5：显式结束
        if act["type"] == "finish":
            print("  步骤{} Thought: {}".format(step, thought))
            print("         [闸5 finish] 答案：{}".format(act["answer"][:120]))
            return

        # 闸3：查重 —— 同一工具 + 同一参数连续出现，说明它在原地打转
        key = (act["name"], json.dumps(act["args"], sort_keys=True, ensure_ascii=False))
        if key in seen:
            print("  步骤{} [闸3 查重] 重复调用 {}({}) → 注入提示并强制结束".format(
                step, act["name"], json.dumps(act["args"], ensure_ascii=False)))
            break
        seen.add(key)

        print("  步骤{} Thought: {}".format(step, thought))
        print("         Action : {}({})".format(
            act["name"], json.dumps(act["args"], ensure_ascii=False)))

        # 真正执行 —— 在应用侧，不在模型侧
        obs = execute(act["name"], act["args"])
        used += len(str(obs))
        print("         Observe: {}".format(str(obs)[:120]))

        # 结果以 role=tool 回填，带着 tool_call_id 再问模型一次
        history.append({"name": act["name"], "args": act["args"], "obs": obs})
    else:
        print("  [闸1 步数] 跑满 {} 步仍未结束，强制收尾".format(MAX_STEPS))


def safety_demo():
    print("\n" + "=" * 60)
    print("参数校验 / 安全边界演示")
    print("=" * 60)
    cases = [
        ("模型编了个不存在的工具", "drop_table", {}),
        ("参数类型不对", "calc_fine", {"days": "abc", "books": 1}),
        ("参数值非法", "calc_fine", {"days": -3, "books": 1}),
        ("缺参数", "query_borrow", {}),
        ("写操作没经人确认", "update_record", {"student_id": "2024001"}),
    ]
    for desc, name, args in cases:
        print("  {:16s} → {}".format(desc, execute(name, args)))
    print("  写操作已确认       → {}".format(
        execute("update_record", {"student_id": "2024001"}, confirmed=True)))


def main():
    print("最小 ReAct Agent（规则模拟模型决策，零依赖）")
    print("五道闸：步数 / 超时 / 查重 / 预算 / finish")

    run("我借的书逾期 12 天，要交多少罚金？", decide, "1 正常流程")
    run("我借的书逾期 12 天，要交多少罚金？", decide_stuck, "2 故意死循环")
    safety_demo()

    print("\n" + "=" * 60)
    print("流程一句话：模型只提申请 → 应用侧校验并执行 → 结果回填 → 再问一次")
    print("=" * 60)


if __name__ == "__main__":
    main()
