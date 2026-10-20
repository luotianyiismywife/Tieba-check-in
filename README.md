# 百度贴吧自动签到（Python 版）

从 `cookie.txt` 读取 BDUSS，自动完成贴吧一键签到 + 逐吧补签。

## 功能

- **一键签到**（msign）：批量签到，被限速（340011）时自动重试
- **逐吧补签**：拉取关注列表（forumGuide 分页），对未签到的贴吧逐个签到
- **并发签到**：8 线程并发，1700 个贴吧约 5 分钟完成
- **网络容错**：超时/连接失败自动重试，不中断任务
- **绕过系统代理**：代理会导致 `c.tieba.baidu.com` SSL 握手超时，脚本已禁用系统代理

## 使用方法

### 1. 安装依赖

```bash
pip install requests
```

### 2. 配置 BDUSS

在脚本同目录创建 `cookie.txt`，写入 BDUSS（支持以下任意格式）：

```
BDUSS=你的BDUSS值
```

或直接粘贴 BDUSS 值，或 Netscape cookies.txt 格式。

> 获取 BDUSS：浏览器登录贴吧 → F12 → Application → Cookies → 复制 `BDUSS`

### 3. 运行

```bash
python tieba_sign.py
```

## 配置项（脚本顶部常量）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `CONCURRENCY` | 8 | 并发签到线程数 |
| `SLEEP_MS` | 600 | 基础请求间隔（毫秒） |
| `MSIGN_RETRY` | 1 | 一键签到限速重试次数 |
| `MSIGN_RETRY_WAIT` | 5 | 重试等待秒数 |
| `MAX_RN` | 200 | 关注列表每页数量 |

## 实现说明

参考 [MoeNetwork/Tieba-Cloud-Sign](https://github.com/MoeNetwork/Tieba-Cloud-Sign) 的实现：

- **签到接口**：`http://c.tieba.baidu.com/c/c/forum/sign`（注意不是 `tiebac.baidu.com`，后者直连会超时）
- **签到参数**：`BDUSS` + `kw`（贴吧名）+ `fid`（forum_id，从关注列表获取）+ `tbs`
- **签名算法**：参数按名排序拼接 + `tiebaclient!!!` 后取**大写** MD5
- **已签到判定**：错误码 `160002` / `1101` / `1102` 均视为已签到
- **无法签到的贴吧**：返回 340006（"贴吧目录出问题啦"）的贴吧为被合并/被封贴吧，属正常现象

## 输出示例

```
获取 tbs ...
tbs 获取成功
一键签到跳过：msign errno=340011 msg=你签得太快了...
共发现未签到贴吧 1693 个
[1/1693] ✓ win10
[2/1693] ✓ 剑网3
...
完成。成功 1650，已签/跳过 0，失败 43
```

## 常见问题

- **msign 340011（签得太快）**：百度服务端频控，脚本会自动降级到逐吧补签，不影响结果
- **SSL 握手超时**：确认脚本未走系统代理（已内置 `trust_env = False`）
- **BDUSS 失效**：重新登录贴吧获取新 BDUSS 更新 `cookie.txt`
