# MVP Sprint Plan — 周末 16 小时

> 目标：周末两天打通端到端 `earnings_recap` 工作流，输入 ticker → 输出 markdown brief + 价格图。
>
> Done 标准：`mhfa earnings-recap NVDA` 在 2 分钟内出一份能看的 brief；5 个 golden brief；factuality eval 跑通；CI 绿；v0.1 release tag。

---

## 准备工作（周五晚上，1 小时，可选）

提前做完这一小时，周末就能纯写代码。

- [ ] 在 Anthropic Console 拿一个新 API key（不要复用 Multi-Horizon 的，这样 cost dashboard 上能干净分项）
- [ ] 在 Tavily 注册免费账号拿 API key（1000 q/月足够）
- [ ] 拿 SEC EDGAR User-Agent 字符串：`"Ruoxuan Song ruoxuan.song@example.com"`（SEC 强制要求 contact info）
- [ ] 选好 5 个 dogfood ticker（你持仓里挑，最好覆盖不同行业）：
  - 例如 `NVDA / META / JPM / XOM / WMT`
- [ ] 在本地建好 `~/.config/mhfa/.env`：
  ```
  ANTHROPIC_API_KEY=...
  TAVILY_API_KEY=...
  SEC_USER_AGENT="Ruoxuan Song email@example.com"
  ```

---

## Day 1 上午（4h）— 基础设施 + 三个 tool

### Hour 1：Repo 初始化（60 min）

**目标**：仓库结构、依赖、CI skeleton 全部就位。

```bash
gh repo create multi-horizon-financial-agent --public --license MIT
git clone ... && cd multi-horizon-financial-agent
```

**创建结构**：
```
src/mhfa/
├── __init__.py
├── tools/
│   ├── __init__.py
│   ├── sec_edgar.py
│   ├── market_data.py
│   ├── search.py
│   └── server.py             # MCP server entry, 周末暂时只是 thin wrapper
├── agent/
│   ├── __init__.py
│   ├── planner.py
│   ├── executor.py
│   └── synthesizer.py
├── models/
│   ├── __init__.py
│   └── client.py
├── workflows/
│   ├── __init__.py
│   └── earnings_recap.py
├── eval/
│   ├── __init__.py
│   └── factuality.py
└── cli.py
configs/
└── models.yaml
eval/
└── golden/                   # 5 个手写 brief，hour 13 填
tests/
├── test_sec_edgar.py
├── test_market_data.py
└── test_search.py
.github/workflows/ci.yml
pyproject.toml
README.md
DECISIONS.md
.env.example
```

**`pyproject.toml` 关键依赖**：
```toml
[project]
name = "mhfa"
requires-python = ">=3.11"
dependencies = [
  "anthropic>=0.40",
  "mcp>=1.0",
  "sec-edgar-downloader",
  "yfinance",
  "tavily-python",
  "matplotlib",
  "pyyaml",
  "python-dotenv",
  "pydantic",
]
[project.scripts]
mhfa = "mhfa.cli:main"
[project.optional-dependencies]
dev = ["pytest", "pytest-mock", "ruff", "mypy"]
```

**最小 CI**（`.github/workflows/ci.yml`）：
- Python 3.11
- `uv sync` → `ruff check` → `pytest`
- 必须周末就跑通，否则后面会拖

**完成判据**：`uv run pytest` 能跑（即使没有 test 也要能跑），`ruff check src/` 通过。

---

### Hour 2：SEC EDGAR tool（60 min）

**File**: `src/mhfa/tools/sec_edgar.py`

```python
def fetch_latest_10q(ticker: str) -> dict:
    """Fetch latest 10-Q filing for a ticker.

    Returns:
        {
          "ticker": str,
          "filing_date": str (ISO),
          "url": str,
          "accession_number": str,
          "sections": {
            "mda": str,                    # Management Discussion & Analysis
            "financial_statements": str,    # 主表
            "risks": str,                   # Item 1A
          },
          "raw_text_truncated": str,        # 前 50k 字符 fallback
        }
    """
```

**实现要点**：
- 用 `sec-edgar-downloader` 拿 filing
- 用 BeautifulSoup parse HTML，按 Item 切分
- **必须**设置 `User-Agent` header（你 `.env` 里的）
- 单个 filing 完整 text 经常 200k+ token，要做 section 切分；万一切分失败给 `raw_text_truncated` 兜底
- 加 1 个 unit test: mock SEC API，测 happy path + 失败路径

**踩过的坑**（提前避雷）：
- SEC 拒绝没有 User-Agent 的请求，状态码是 200 但内容是 "Please use a meaningful contact info"
- 10-Q 的 section 名称在不同公司之间不一致（"Risk Factors" vs "Item 1A. Risk Factors" vs "ITEM 1A"），用 fuzzy match
- 有些公司只发 10-K（年报），季度发 8-K；要兼容这种情况

**完成判据**：`fetch_latest_10q("NVDA")` 在终端能跑出至少 mda / risks 两个 section 各 5k+ 字符。

---

### Hour 3：Market data tool（60 min）

**File**: `src/mhfa/tools/market_data.py`

```python
def get_quote_history(ticker: str, period: str = "3mo") -> dict:
    """Historical OHLCV + summary stats via yfinance.

    Returns:
        {
          "ticker": str,
          "period": str,
          "prices": [{"date": str, "open": float, "high": float,
                      "low": float, "close": float, "volume": int}, ...],
          "summary": {
            "start_close": float,
            "end_close": float,
            "pct_change": float,
            "high_52w": float,
            "low_52w": float,
            "avg_volume": int,
          }
        }
    """

def get_company_info(ticker: str) -> dict:
    """Company name, sector, industry, market cap, current valuation multiples."""
```

**实现要点**：
- yfinance 是 unofficial wrapper，**经常断**。包一层 `try/except` + retry
- 周末不需要 fallback（Phase 2 再加 Alpha Vantage），但要记录 failure 到日志
- summary 字段要预先算好，不要让 LLM 自己算（容易幻觉）
- chart 生成函数也放这里：`def plot_price_history(prices, save_path)` 用 matplotlib，保存为 PNG

**踩过的坑**：
- yfinance 偶尔返回空 DataFrame 还不报错，要显式检查 `len(df) == 0`
- 美股是 trading day 不是 calendar day，period="3mo" 大约是 63 个 row 不是 90

**完成判据**：5 个 ticker 都能跑出 summary + 3 个月图（PNG 文件存在）。

---

### Hour 4：Web search tool + Anthropic client wrapper（60 min）

**File**: `src/mhfa/tools/search.py`

```python
class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 5) -> list[dict]: ...

class TavilySearch:
    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Returns: [{"title": str, "url": str, "snippet": str, "content": str}, ...]"""
```

**关键**：抽象出 `SearchProvider` interface，未来切到 Gemini grounding 不影响 agent 层。这是 D-004 决策。

**File**: `src/mhfa/models/client.py`

```python
def get_client(role: Literal["planner", "tool_synth", "synthesizer", "judge"]) -> Anthropic:
    """Returns anthropic client + the model name configured for this role.
    Reads from configs/models.yaml.
    """
```

**`configs/models.yaml`**：
```yaml
roles:
  planner:
    model: claude-sonnet-4-6
    max_tokens: 2000
  tool_synth:
    model: claude-sonnet-4-6
    max_tokens: 4000
  synthesizer:
    model: claude-opus-4-7
    max_tokens: 8000
  judge:
    model: claude-haiku-4-5
    max_tokens: 1000
```

**完成判据**：在 REPL 里能 `client = get_client("synthesizer")` 然后 `client.messages.create(...)` 拿到回复。

---

## Day 1 下午（4h）— Agent loop

### Hour 5：Planner（60 min）

**File**: `src/mhfa/agent/planner.py`

```python
@dataclass
class ToolCall:
    tool: str          # "sec.fetch_latest_10q" / "market.get_quote_history" / ...
    args: dict
    reason: str        # 为什么调这个 tool（debug + transparency 用）

def build_plan(query: str, available_tools: list[dict],
               context: dict | None = None) -> list[ToolCall]:
    """Use Claude Sonnet to build an ordered tool plan for a financial query.
    Hardcoded for earnings_recap workflow in MVP.
    """
```

**实现要点**：
- 用 Claude 的 native tool use API（不要自己造 prompt parser）
- 用 structured output: 让 Claude 输出一个 JSON list
- MVP 阶段 plan 几乎是固定的（10-Q + 3mo 价格 + recent news），但仍走 LLM —— 这样 Phase 2 加新 workflow 不用重写
- system prompt 强调 "be exhaustive but no redundant calls"

**踩过的坑**：
- Claude 偶尔会包额外的 prose 在 JSON 外面，用 structured output / tool use schema 强制
- planner 返回的 args 必须 validate（pydantic），ticker 可能写成 `"NVDA Corp"`

**完成判据**：`build_plan("Q3 NVDA earnings recap")` 返回 3–4 个 tool call 的 list，所有 args 通过 pydantic 校验。

---

### Hour 6：Executor（60 min）

**File**: `src/mhfa/agent/executor.py`

```python
def execute_plan(plan: list[ToolCall]) -> dict[str, Any]:
    """Execute each ToolCall, return aggregated dict keyed by tool name.

    Returns:
        {
          "sec.fetch_latest_10q": {...},
          "market.get_quote_history": {...},
          "search.web_search": [{...}, ...],
          "_metadata": {
            "duration_ms": int,
            "errors": [...],     # tool failures don't crash, get logged
          }
        }
    """
```

**实现要点**：
- MVP 不并行（先正确，再快）；Phase 3 再加 asyncio
- 单个 tool 失败不要整个 crash —— 写到 `_metadata.errors`，让 synthesizer 知道某条信息缺失
- 每个 tool call 记录耗时 + 简单 cost（estimate token）

**完成判据**：`execute_plan(plan)` 返回 dict 包含三个 key，`errors` 列表为空。

---

### Hour 7：First end-to-end smoke run（60 min）

**目标**：拼起来跑通 NVDA。预期会有 3–5 个 bug。

**临时入口** `src/mhfa/workflows/earnings_recap.py`（先写极简版）：
```python
def run_earnings_recap(ticker: str) -> dict:
    query = f"Quarterly earnings recap for {ticker}"
    plan = build_plan(query, available_tools=AVAILABLE_TOOLS)
    raw = execute_plan(plan)
    # synthesizer 这一小时还没写，先 print 看看 raw 长什么样
    return {"plan": plan, "raw": raw}
```

**预期会踩的坑**：
- planner 调 tool 的 args 名字跟 executor 期望的对不上（用枚举/常量统一）
- SEC text 太长，超 Claude context（先粗暴截断到 30k token）
- yfinance 偶尔 hang —— 加 timeout

**完成判据**：能从 `run_earnings_recap("NVDA")` 拿到非空 raw，3 个 tool 都成功。

---

### Hour 8：迭代到 1 个 ticker 干净跑通（60 min）

**这一小时是 buffer**。一定会踩坑，至少留 60 min 调试。如果 hour 7 已经干净，这一小时改成：
- 跑剩下 4 个 ticker，记录每个的 failure mode
- 修最常见的 1–2 个

**完成判据**：5 个 ticker 都能跑完 plan + execute（synthesizer 还没写），`_metadata.errors` 都是空或可接受的 known issue。

---

## Day 2 上午（4h）— Synthesizer + 输出

### Hour 9：Synthesizer（60 min）

**File**: `src/mhfa/agent/synthesizer.py`

```python
def synthesize_brief(query: str, raw_data: dict, ticker: str,
                     model_role: str = "synthesizer") -> str:
    """Generate a markdown brief from raw tool data.
    Returns: markdown string.
    """
```

**brief 模板（强约束让模型按结构来）**：
```markdown
# {Ticker} — {Quarter} Earnings Recap

## Executive Summary
（3–4 句话，含数字）

## Financial Highlights
| Metric | Value | YoY |
|---|---|---|
...

## Price Action (3mo)
![](chart.png)
{1 段评论}

## Recent Catalysts
- ...

## Key Risks
- ...

## Sources
- 10-Q filed YYYY-MM-DD: <url>
- Market data: yfinance, retrieved YYYY-MM-DD
- News: <urls>
```

**实现要点**：
- system prompt 强约束："Every numeric claim MUST cite a source from the raw_data. If you cannot verify a number, write 'not disclosed'."
- 用 Opus 4.7（这是用户实际看的输出，不省钱）
- 把 raw_data 整体塞进去（30k+ token 没事，Opus context 够）

**完成判据**：5 个 ticker 都能出 brief，初步看起来"像那么回事"（有数字、有结构、不空洞）。

---

### Hour 10：Chart 嵌入（60 min）

- `plot_price_history` 输出 PNG 到 `outputs/{ticker}_{quarter}_price.png`
- synthesizer 在 markdown 里 reference 相对路径
- CLI 输出整个 brief + chart 到一个 zip 或一个 directory

**踩过的坑**：matplotlib 默认字体丑；用 seaborn 主题或简单设 `plt.style.use('ggplot')`。

**完成判据**：打开 markdown，chart 显示正常，颜色清楚，标题和 axis 完整。

---

### Hour 11：5 个 ticker 完整跑 + 人工 review（60 min）

跑完 5 个 ticker，每个 brief 自己读一遍，记录：
- 哪些数字看起来不对
- 哪些应该提没提（漏 catalyst）
- 哪些是 LLM 编的（hallucination）

不要急着修，**这一小时只做 observation**。bug list 写到 `eval/dogfood_notes.md`。

---

### Hour 12：Bug fix（60 min）

按 bug 严重度排序，修 top 3。如果某些 bug 是 fundamental 的（比如 SEC text 切分错，导致 risks section 一直缺），优先修这种。

剩下的 bug 写进 `DECISIONS.md` 的 "Known limitations" 段。

---

## Day 2 下午（4h）— Eval + 文档 + 推送

### Hour 13：5 个 golden brief（60 min）

**File**: `eval/golden/{TICKER}_{QUARTER}.md`

**写法**：
- 不是 LLM 生成（避免 evaluator-generator collapse, D-006）
- 你自己读 10-Q + yfinance + 1–2 篇新闻，按上面的 brief 模板手写
- 每个 ~15 min。第一个写 25 min，后面熟练后 10 min。
- 这是这一小时最累但最值的活——**这是 portfolio 里"严肃做 eval"的真实证据**

**捷径**：可以让 Claude 起草，但**所有数字你自己核**。最终签名是你的。

**完成判据**：5 个 .md 文件，每个 200–400 字，所有数字有 source。

---

### Hour 14：Factuality eval（60 min）

**File**: `src/mhfa/eval/factuality.py`

```python
def check_factuality(brief: str, raw_data: dict,
                     judge_role: str = "judge") -> dict:
    """LLM-as-judge: trace each numeric claim in brief back to raw_data.

    Returns:
        {
          "score": float,          # verified / total claims
          "total_claims": int,
          "verified_claims": int,
          "flagged": [             # claims that couldn't be verified
            {"claim": str, "reason": str},
            ...
          ]
        }
    """
```

**实现思路**：
1. Haiku 第一遍：从 brief 里抽出所有 numeric / factual claims（list）
2. Haiku 第二遍：每个 claim → 在 raw_data 里 search → "verified" / "not_found" / "contradicted"
3. 聚合 → score

**MVP 跑出 baseline 数字**（5 个 brief 平均 factuality）写到 README。

**完成判据**：跑通 5 个 brief，输出 `eval/runs/run_v0.1.json`，README 里有一句 "MVP baseline: factuality = 0.XX on 5-brief sample"。

---

### Hour 15：CI + README + DECISIONS（60 min）

**`README.md`**（结构）：
1. Hero 段：1 张 architecture 图（用 mermaid 或 ASCII） + 一句话定位
2. Quickstart：3 步装好，1 行命令出 brief
3. Example：贴一份 NVDA brief 截图（或链接）
4. Eval：MVP baseline 数字 + "see EVAL.md for methodology"
5. Roadmap：链到 ROADMAP.md
6. Sister project：链到 `multi-horizon-financial-llm`
7. License (MIT)

**`DECISIONS.md`**：把 ROADMAP 第 4 节的 D-001 ~ D-007 抄过来，每条写 2–3 句具体的 reasoning。

**CI**：确认 `pytest` + `ruff check` 都过，绿勾贴 README。

---

### Hour 16：Push + release + 复盘（60 min）

```bash
git add . && git commit -m "feat: MVP v0.1 — earnings_recap end-to-end"
git push
git tag v0.1.0
git push --tags
gh release create v0.1.0 --notes-file release_notes.md
```

**`release_notes.md`** 可以写：
- What works: earnings_recap on any S&P 500 ticker, 5 golden briefs, factuality baseline
- Known limits: yfinance flakiness, single workflow, no MH adapter integration yet
- Next: Phase 2 — adapter integration + A/B harness

**最后 30 min 复盘** —— 在 `dogfood_notes.md` 续写：
- 这周末最痛的 1 个 bug 是什么
- 哪 2 个架构决策最值得回顾
- Phase 2 哪个 task 最该先做

---

## Stop conditions（什么时候停下来不修了）

防止你陷入完美主义。MVP 不是最终产品。

| 症状 | 行动 |
|---|---|
| 单个 bug 调了 60+ min | 写到 Known Limitations，Phase 2 再修 |
| 某个 ticker 永远 fail（比如 ADR、退市股）| 限定 MVP scope 到 S&P 500，README 说明 |
| LLM 输出看起来"还行但不完美" | 留着，Phase 2 加 eval 来量化 |
| "我想加这个 feature" | 写到 ROADMAP Phase 3，**不要这周末做** |
| 已经周日下午 7 点了 | 立刻停，push 当前状态，README 标 WIP |

---

## 周末后立刻做的两件事（周一 30 min）

1. 把这个 repo 加到简历底部 portfolio 段，写 "WIP, MVP working" + 链接
2. LinkedIn / 小红书发一条 ~80 字短帖介绍，贴 brief 截图。**不要等到完美再发，发了就有人看，看了就有 momentum**。

---

## Cheat sheet：关键命令

```bash
# 开发
uv sync
uv run pytest
uv run ruff check src/
uv run mypy src/mhfa --strict

# 跑一次
uv run mhfa earnings-recap NVDA --output ./outputs/

# Eval
uv run python -m mhfa.eval.factuality --brief outputs/NVDA.md --raw outputs/NVDA_raw.json
```

---

## 修订历史
- v0.1 — initial sprint plan
