# SSE 并发压测

`scripts/load_test_sse.py` 会调用真实的 Session 创建、SSE Chat 和 Session 删除端点；不会保存或打印 token、prompt、answer。

压测前需启动 API，完成迁移，为测试用户保存可用的 LLM 设置，并提高测试环境的限流：

```env
RATE_LIMIT_CHAT_STREAM="500 per minute"
RATE_LIMIT_SESSION="500 per minute"
```

登录获得 user token 后，仅通过当前终端的环境变量传入：

```powershell
$env:QAAGENT_USER_TOKEN = "<user bearer token>"
uv run python scripts/load_test_sse.py --base-url http://127.0.0.1:8001 --levels 20 50 100
Remove-Item Env:QAAGENT_USER_TOKEN
```

每个并发级别使用独立 Session。结果表只包含成功率、首个 SSE 事件延迟、总延迟和吞吐量；任一请求失败时脚本返回非零退出码。该测试会产生真实模型调用费用。
