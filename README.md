# ncwu-notice-monitor

每天自动检查 [华北水利水电大学工程实践与创新中心 · 通知公告](https://www2.ncwu.edu.cn/gcxlzx/zxdt/tzgg.htm)
有没有新公告，有就推到微信。

## 它是怎么判断"有没有新公告"的

列表页每条公告的长这样：

```html
<li class="media" onclick="location='../info/1027/2168.htm'">
  <span class="day">10-09</span><span class="year">2026</span>
  <h5 class="title">关于2026年创新创业实践学分系统…</h5>
</li>
```

注意两点：

1. **链接不在 `<a href>` 里**，而在 `onclick` 属性里；
2. 里面的 **`2168` 是文章 ID，单调递增、永不复用** —— 这是最可靠的"新内容"判据。

所以判定逻辑就是：**取列表里最大的文章 ID，和历史记录里的最大值比，变大了就是有新公告。**

> 为什么不按"第一条"判断？因为列表第 1 条是**置顶**公告（ID 2153、日期 09-03），
> 它永远排在最前面，拿它当"最新"会天天误报。

## 文件说明

| 文件 | 作用 |
|---|---|
| `monitor.py` | 全部逻辑，纯标准库，零依赖 |
| `.github/workflows/check.yml` | 每天定时跑，跑完把状态回写进仓库 |
| `state.json` | 历史快照（已见过的公告）。**别删，这是唯一的断点** |
| `部署说明.md` | 完整的方案说明与备选实现方式 |

## 本地使用

```bash
python monitor.py            # 日常检查，有新增就推通知
python monitor.py --show     # 只看当前列表，不读写状态
python monitor.py --push-test  # 发一条测试通知，验证渠道通不通
python monitor.py --reset    # 清空历史重建基线（不会推送）
```

**退出码**：`0` 正常 ｜ `2` 抓取失败 ｜ `3` 页面改版导致解析到 0 条 ｜ `4` 推送全部失败

## 配置通知渠道

在仓库 `Settings → Secrets and variables → Actions` 里加 Secret，脚本会自动读取：

| Secret 名 | 渠道 | 获取地址 |
|---|---|---|
| `SERVERCHAN_KEY` | 微信（Server酱） | https://sct.ftqq.com 微信扫码 |
| `PUSHPLUS_TOKEN` | 微信（PushPlus，免费额度更大） | https://pushplus.plus 微信扫码 |
| `BARK_KEY` | iPhone 推送（Bark） | App Store 装 Bark 后获取 |
| `WEBHOOK_URL` | 企业微信 / 钉钉 / 飞书机器人 | 群设置里拿 Webhook |

留空即关闭，可以同时配多个。想验证配好没有：`Actions → 通知公告监测 → Run workflow`
（默认勾了"只做推送测试"）。

## 注意事项

- **定时不准**：GitHub 的 cron 是最佳努力，高峰期常延迟 5~30 分钟，属正常现象。
- **页面改版是最大失效点**：一旦解析到 0 条会直接报错退出（码 3），**不会**静默判成"无更新"。
  如果 Actions 一直标红，就是模板变了，改 `monitor.py` 里的 `ITEM_RE / TITLE_RE` 即可。
- **置顶公告不会造成误报**（见上文），但如果你改回"按第一条判断"就会。
- **定时任务会被自动禁用**：仓库 60 天无活动时 GitHub 会停掉 schedule，
  工作流里加了 45 天心跳提交来规避。
- 本站实测无 `robots.txt`、无验证码、无频率限制，每天抓 1 次完全没问题。
