# 数字人 API 对接文档

本文仅面向业务对接。下文列出的接口就是对接范围；未出现的路径请勿调用、勿依赖。

公网 Base URL（对接请用这个）：

```text
http://36.136.54.165:8888/QeMixAvatar
```

连通性自检：

```bash
curl -s http://36.136.54.165:8888/QeMixAvatar/api/system/ready
```

所有路径均为相对 Base URL，例如列出公共形象：

```text
http://36.136.54.165:8888/QeMixAvatar/api/avatars?type=public
```

**不要用下面这些地址，会连不上或 404：**

| 错误写法 | 结果 |
|---|---|
| `http://36.136.54.165:8811/...` | 公网未开放 8811，**Connection refused** |
| `http://36.136.54.165:8888/api/...`（缺少 `/QeMixAvatar`） | 代理 **404** |
| `https://36.136.54.165:8888/...` | 该端口不是 HTTPS |
| `http://<内网IP>:8811/...` | 仅机房内网可达，第三方公网访问不到 |

网页控制台与 API 共用同一 Base URL：[http://36.136.54.165:8888/QeMixAvatar](http://36.136.54.165:8888/QeMixAvatar)

---

## 1. 通用约定

### 1.1 响应格式

除媒体接口（封面、预览视频、成片预览/下载）外，全部使用统一信封。业务失败时 **HTTP 状态码仍为 `200`**，以 `code` / `success` 判断。

成功：

```json
{
  "code": 0,
  "msg": "ok",
  "message": "ok",
  "success": true,
  "data": {}
}
```

失败：

```json
{
  "code": 1,
  "msg": "错误说明",
  "message": "错误说明",
  "success": false,
  "data": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | `0` 成功，非 `0` 失败 |
| `msg` / `message` | string | 提示信息，两字段同义 |
| `success` | bool | 是否成功 |
| `data` | object / array / null | 成功为业务数据，失败为 `null` |

媒体接口成功时直接返回文件二进制（HTTP `200`）；失败为 HTTP `4xx`，正文：

```json
{ "detail": "错误说明" }
```

时间字段均为 UTC，格式 `YYYY-MM-DDTHH:MM:SSZ`。

创建作品只表示**已进入队列**。请用「查询作品」轮询 `status`，不要把创建成功当成合成完成。

### 1.2 用户标识

`username` 与 `user_id` 表示**同一个用户 ID**，最长 64 个字符，禁止 `\ / : * ? " < > |`。

| 场景 | 怎么传 |
|---|---|
| 查询、删除、重试、取消、重新转码 | Query：`username` 或 `user_id`，二选一 |
| 创建作品 | Form 字段名固定为 `username`（必填，公共形象也要填） |
| 读取个人封面 / 形象视频 / 个人成片 | 请求头 `X-User-Id` 或 `X-Username`，必须与所有者一致 |

请在对接侧为每个终端用户分配稳定 ID，并在上述位置使用同一值。

**列表、详情 JSON 的 Query 不能用来打开文件。** 个人素材和成片的 URL 即使带了 `?user_id=` 也打不开。

### 1.3 形象类型

| 取值 | 说明 |
|---|---|
| `public` | 公共形象，所有对接方可列表、预览、用于合成 |
| `private` | 个人形象，必须绑定用户 ID；只能被该用户使用 |

新建形象 ID 为 **8 位**小写十六进制，例如 `a1b2c3d4`。已有长 ID 仍可继续使用。

### 1.4 形象状态

请以 `bake_status` 为准。

| `bake_status` | 含义 | 能否创建作品 |
|---|---|---|
| `processing` | 转码中 | 否 |
| `ready` | 已就绪 | 是 |
| `error` | 转码失败，可调用重新转码 | 否 |
| `missing` | 尚未生成可用视频 | 否 |

同对象里的 `status` 为内部值（`queued` / `preparing` / `aligning` / `ready` / `failed`），对接以 `bake_status` 为准即可。

### 1.5 作品状态

| `status` | 含义 |
|---|---|
| `wait` | 排队中 |
| `run` | 合成中 |
| `done` | 已完成，可预览/下载 |
| `cancelled` | 已取消，可重试 |
| `error` | 合成失败，可重试 |

查询列表的 `status` 也接受 `queued` / `running` / `failed` / `cancelled` / `active`（排队中+合成中），返回值仍是上表。取消成功后 `status` 为 `cancelled`，界面和接口都显示已取消，不会变成 `error`。只有真正合成失败才是 `error`。

### 1.6 合成质量

| `steps` | `quality_label` |
|---|---|
| `30` | 标准（默认） |
| `50` | 高质量 |
| `80` | 超高质量 |

只接受以上三个取值。

形象 `preview_video_path` 为低码率预览：原分辨率、约 2Mbps、**静音**。成片 `result_path` 同样为原分辨率、约 2Mbps，但**保留声音**。无损原片请用下载接口。

### 1.7 素材限制

| 种类 | 支持后缀 | 大小上限 |
|---|---|---|
| 形象视频 | `.mp4` `.mov` `.mkv` `.webm` `.avi` | 2GB |
| 驱动音频 | `.wav` `.mp3` `.m4a` `.aac` `.flac` `.ogg` | 300MB |

### 1.8 个人库隔离（媒体必须带头）

个人形象的封面、形象视频，以及使用了个人形象的成片：

- 接口返回的地址**不会**带 `user_id` 查询参数，把该地址转发给别人无效。
- 浏览器或 curl **单独打开该 URL 无效**（HTTP `404`）。
- URL 上的 `?user_id=` / `?username=` **不能**解锁文件。
- 必须加请求头，且值等于该形象/作品所属用户：

```http
X-User-Id: u1001
```

`X-Username` 与 `X-User-Id` 同义。

公共形象的封面和视频、使用公共形象且可公开访问的成片，可直接 GET，无需该头。

`<img src>` / `<video src>` **无法自定义请求头**。对接个人库时，请由你们的服务端带 `X-User-Id` 拉取后再转发给自己的前端，或拉成 blob 再播放。不要把个人媒体地址直接给最终用户当可分享链接。

---

## 2. 推荐对接流程

1. `GET /api/avatars?type=public` 选用公共形象，或按 4.2 上传个人形象（多个视频可并行，见 4.2）。
2. 个人形象上传后轮询列表，直到该条 `bake_status=ready`。
3. `POST /api/tasks/create` 提交音频，拿到 `task_id`。
4. `GET /api/tasks/{task_id}?username=<用户ID>` 轮询，直到 `status=done`、`error` 或 `cancelled`。排队或合成中可 `POST /api/tasks/{task_id}/cancel` 取消；成功后该条即为 `cancelled`（不是 `error`），队列里下一条会自动开始，不必为取消再轮询。
5. `status=done` 后：预览用 `result_path`（低码率），保存原片用 `GET /api/tasks/{task_id}/download`。个人成片两次请求都要带 `X-User-Id`。

---

## 3. 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/ready` | 检查服务是否可调用（连通性） |
| GET | `/api/avatars` | 分页列出形象 |
| POST | `/api/avatars/upload` | 分片上传形象（init / chunk / complete / abort；多文件并行即批量） |
| POST | `/api/avatars/{identifier}/rebake` | 转码失败后重新转码 |
| DELETE | `/api/avatars/{identifier}` | 删除形象 |
| GET | `/api/characters/{id}/poster` | 形象封面（jpeg） |
| GET | `/api/characters/{id}/video` | 形象预览视频（mp4，低码率） |
| GET | `/api/tasks` | 分页列出作品 |
| POST | `/api/tasks/create` | 创建合成作品 |
| GET | `/api/tasks/{task_id}` | 查询单个作品（轮询） |
| POST | `/api/tasks/{task_id}/cancel` | 取消排队中或合成中的作品 |
| POST | `/api/tasks/{task_id}/retry` | 合成失败或已取消后重试 |
| GET | `/api/tasks/{task_id}/preview` | 成片预览（mp4，低码率，带声音） |
| GET | `/api/tasks/{task_id}/download` | 成片原片下载 |
| DELETE | `/api/tasks/{task_id}` | 删除作品 |

---

### 3.1 检查服务是否就绪

`GET /api/system/ready`（也可用 `HEAD`）

无需鉴权。用于确认公网代理和本服务都活着。`data.ready` 为 `true` 表示 ffmpeg 和模型文件齐全，可以提交合成。

```bash
curl -s http://36.136.54.165:8888/QeMixAvatar/api/system/ready
```

成功时 `data` 含 `ready`、`checks.ffmpeg` / `checks.model` / `checks.gpu`、`gpu_busy`、`worker_alive`。浏览器跨域已允许；带 `X-User-Id` 的预检 `OPTIONS` 也会放行。

---

## 4. 形象接口

### 4.1 分页列出形象

`GET /api/avatars`

**Query**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | 否 | `public` / `private` / `all`。不传或 `all` 且不带用户 ID 时**只返回公共形象** |
| `username` 或 `user_id` | string | 查个人库必填 | 个人形象只返回该用户的。`type=private` 且不传用户 ID 会失败，不会列出其他人的个人库 |
| `bake_status` | string | 否 | `ready` / `processing` / `error` / `missing` |
| `page` | int | 否 | 从 1 起，默认 `1` |
| `page_size` | int | 否 | 默认 `12`，最大 `100` |

**成功 `data`**

```json
{
  "items": [
    {
      "identifier": "a1b2c3d4",
      "id": "a1b2c3d4",
      "name": "新闻主播",
      "type": "public",
      "user_id": null,
      "username": null,
      "status": "ready",
      "error": null,
      "progress": null,
      "duration": 8.32,
      "width": 1920,
      "height": 1080,
      "created_at": "2026-08-30T01:20:00Z",
      "thumbnail": "/api/characters/a1b2c3d4/poster",
      "preview_thumbnail": "/api/characters/a1b2c3d4/poster",
      "video_path": "/api/characters/a1b2c3d4/video",
      "preview_video_path": "/api/characters/a1b2c3d4/video",
      "bake_status": "ready",
      "bake_progress": 100,
      "bake_message": ""
    }
  ],
  "total": 1,
  "page": 1,
  "pages": 1,
  "page_size": 12,
  "counts": { "public": 1, "private": 0 },
  "ready_counts": { "public": 1, "private": 0 }
}
```

| 字段 | 说明 |
|---|---|
| `identifier` / `id` | 形象 ID，两者相同。新建为 **8 位**小写十六进制，例如 `a1b2c3d4` |
| `thumbnail` / `preview_thumbnail` | 封面相对路径，未生成时为空 |
| `video_path` / `preview_video_path` | 预览视频相对路径，未就绪时为空 |
| `counts` / `ready_counts` | 在当前用户范围下的公共/个人数量（未传用户 ID 时个人为 0） |

`counts.private` 只统计**当前查询用户**的个人形象，不会返回全站个人库数量。

**失败示例**

```json
{
  "code": 1,
  "msg": "查询个人形象必须填写用户ID",
  "success": false,
  "data": null
}
```

```bash
# 公共形象
curl -s "http://36.136.54.165:8888/QeMixAvatar/api/avatars?type=public&page=1&page_size=12"

# 某用户的个人形象
curl -s "http://36.136.54.165:8888/QeMixAvatar/api/avatars?type=private&username=u1001"

# 公共 + 该用户个人
curl -s "http://36.136.54.165:8888/QeMixAvatar/api/avatars?type=all&username=u1001"
```

---

### 4.2 分片上传形象（支持批量）

`POST /api/avatars/upload`  
`Content-Type: multipart/form-data`

**一次会话只处理一个视频。** 批量上传 = 对每个视频各自走完 `init → chunk → complete`，会话之间互不影响，可并行。

建议：

- 同时进行的会话不超过 **2～4** 个（过大容易打满带宽或网关超时）。
- 每个视频单独一个 `upload_id`，名称、类型、用户 ID 按文件分别传。
- 中途取消、失败或客户端断开：立刻调 `stage=abort` 删掉已收分片；未 abort 的半成品，服务端会在 **24 小时无新分片** 后自动清理。
- 不要把多个视频塞进同一次 `init`。没有单独的「批量 ZIP」接口。

分四步，由 `stage` 区分：`init` → `chunk`（可多次）→ `complete`。取消用 `abort`。

#### 4.2.1 初始化 `stage=init`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `stage` | string | 是 | `init` |
| `name` | string | 是 | 形象名称 |
| `type` | string | 否 | 默认 `public` |
| `username` 或 `user_id` | string | 个人必填 | 用户 ID |
| `filename` | string | 是 | 原文件名，用于判断后缀 |
| `filesize` | int | 是 | 文件字节数 |
| `chunk_size` | int | 否 | 64KB～16MB，默认 2MB（2097152） |

成功 `data`：

```json
{
  "upload_id": "7c9e6679b3d84c3e8f1a2b3c4d5e6f70",
  "total_chunks": 5,
  "chunk_size": 2097152
}
```

| `msg` | 场景 |
|---|---|
| `请填写形象名称` | `name` 为空 |
| `形象名称不能超过 80 个字符` | `name` 过长 |
| `个人形象必须填写用户ID` | `type=private` 未传用户 ID |
| `用户ID不能超过 64 个字符` | 超长 |
| `用户ID无效` | 清洗后为空 |
| `形象类型只能是 public（公共）或 private（个人）` | `type` 非法 |
| `不支持的视频格式：.xxx` | 后缀不在允许列表 |
| `视频大小需在 1B–2GB 之间` | `filesize` 非法 |
| `分片大小无效` | `chunk_size` 越界 |

```bash
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/avatars/upload \
  -F stage=init \
  -F name=新闻主播 \
  -F type=private \
  -F username=u1001 \
  -F filename=host.mp4 \
  -F filesize=$(stat -c%s host.mp4) \
  -F chunk_size=2097152
```

#### 4.2.2 上传分片 `stage=chunk`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `stage` | string | 是 | `chunk` |
| `upload_id` | string | 是 | 初始化返回的 ID |
| `chunk_index` | int | 是 | 从 `0` 起 |
| `total_chunks` | int | 否 | 总分片数 |
| `chunk` | file | 是 | 本片二进制 |

非最后一片必须正好 `chunk_size` 字节；最后一片必须等于 `filesize - chunk_size * (total_chunks - 1)`。

成功 `data`：

```json
{
  "index": 0,
  "received": 1,
  "total_chunks": 5
}
```

| `msg` | 场景 |
|---|---|
| `分片参数不完整` | 缺少 `upload_id` / `chunk` / `chunk_index` |
| `上传任务不存在` | `upload_id` 无效 |
| `分片总数与初始化不一致` | 传了 `total_chunks` 但与 init 不一致 |
| `空分片` | 内容为空 |
| `分片大小应为 N 字节` | 非最后一片大小不对 |
| `最后分片大小应为 N 字节` | 最后一片大小不对 |

```bash
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/avatars/upload \
  -F stage=chunk \
  -F upload_id=<上传ID> \
  -F chunk_index=0 \
  -F total_chunks=<总分片> \
  -F chunk=@chunk0.bin
```

#### 4.2.3 完成上传 `stage=complete`

| 参数 | 类型 | 必填 |
|---|---|---|
| `stage` | string | `complete` |
| `upload_id` | string | 是 |

成功后形象进入转码队列，`msg` 为 `上传成功，正在处理`，`data` 为形象对象（字段同 4.1，此时 `bake_status` 一般为 `processing`）。

请用 4.1 轮询，直到 `bake_status=ready` 再创建作品。

| `msg` | 场景 |
|---|---|
| `缺少 upload_id` | 未传 |
| `上传任务不存在` | ID 无效 |
| `分片不完整` | 未收齐 |
| `分片 N 文件丢失` | 分片文件缺失 |
| `合并后文件大小与声明不一致` | 与 `filesize` 不符 |
| `未知的上传阶段` | `stage` 不是 `init` / `chunk` / `complete` / `abort` |

```bash
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/avatars/upload \
  -F stage=complete \
  -F upload_id=<上传ID>
```

#### 4.2.4 取消并清理分片 `stage=abort`

上传到一半要停、失败后放弃、或客户端断开前，请调用本阶段。会删除该 `upload_id` 下已收到的分片，不创建形象。可重复调用（幂等）。

Form 与 Query 两种写法等价（关页时可用 Query，避免 multipart 发不出去）：

```http
POST /api/avatars/upload
Content-Type: multipart/form-data
stage=abort&upload_id=<上传ID>
```

```http
POST /api/avatars/upload?stage=abort&upload_id=<上传ID>
```

| 参数 | 类型 | 必填 |
|---|---|---|
| `stage` | string | `abort` |
| `upload_id` | string | 是 |

成功 `msg`：`已取消并清理分片`。

| `msg` | 场景 |
|---|---|
| `缺少 upload_id` | 未传 |
| `上传任务不存在` | ID 格式无效 |

```bash
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/avatars/upload \
  -F stage=abort \
  -F upload_id=<上传ID>
```

#### 4.2.5 批量上传示例

两个视频并行（各自独立会话）：

```bash
# 视频 A
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/avatars/upload \
  -F stage=init -F name=主播A -F type=private -F username=u1001 \
  -F filename=a.mp4 -F filesize=$(stat -c%s a.mp4) -F chunk_size=8388608

# 视频 B（可同时发）
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/avatars/upload \
  -F stage=init -F name=主播B -F type=private -F username=u1001 \
  -F filename=b.mp4 -F filesize=$(stat -c%s b.mp4) -F chunk_size=8388608
```

分别对返回的两个 `upload_id` 上传分片并 `complete`。全部进入转码后，用 4.1 按用户 ID 轮询，直到各条 `bake_status=ready`。

---

### 4.3 重新转码

`POST /api/avatars/{identifier}/rebake`

个人形象必须带 Query `username` 或 `user_id`。

成功：

```json
{
  "code": 0,
  "msg": "已重新排队转码",
  "success": true,
  "data": { "identifier": "a1b2c3d4" }
}
```

失败常见 `msg`：`形象不存在`。

```bash
curl -s -X POST "http://36.136.54.165:8888/QeMixAvatar/api/avatars/<形象ID>/rebake?username=u1001"
```

---

### 4.4 删除形象

`DELETE /api/avatars/{identifier}`

个人形象必须带 Query `username` 或 `user_id`。若该形象仍有排队或合成中的作品，不能删除。

成功 `msg`：`已删除`。

| `msg` | 场景 |
|---|---|
| `形象不存在` | ID 无效或不属于该用户 |
| `该形象还有排队或正在合成的任务` | 仍有未完成作品 |

```bash
curl -s -X DELETE "http://36.136.54.165:8888/QeMixAvatar/api/avatars/<形象ID>?username=u1001"
```

---

## 5. 媒体接口

以下地址会出现在形象/作品 JSON 中，请拼在 Base URL 后面请求。均为相对路径，不要依赖或拼接任何磁盘路径。

### 5.1 形象封面

`GET /api/characters/{id}/poster`

- 公共形象：直接 GET。
- 个人形象：必须请求头 `X-User-Id`（或 `X-Username`）。

成功：HTTP `200`，`Content-Type: image/jpeg`。

失败：HTTP `404`，`detail` 为 `形象不存在` 或 `暂无封面`。

```bash
# 公共
curl -o poster.jpg http://36.136.54.165:8888/QeMixAvatar/api/characters/<形象ID>/poster

# 个人（必须带头；下面两种都拿不到文件）
curl -H "X-User-Id: u1001" -o poster.jpg \
  http://36.136.54.165:8888/QeMixAvatar/api/characters/<形象ID>/poster
curl -o poster.jpg http://36.136.54.165:8888/QeMixAvatar/api/characters/<形象ID>/poster
curl -o poster.jpg "http://36.136.54.165:8888/QeMixAvatar/api/characters/<形象ID>/poster?user_id=u1001"
```

### 5.2 形象预览视频

`GET /api/characters/{id}/video`

鉴权同 5.1。成功：`Content-Type: video/mp4`。为原分辨率、约 2Mbps、静音的预览；预览尚未生成时返回 404，不会回退到带声音的原片。

失败：HTTP `404`，`detail` 为 `形象不存在` 或 `形象视频不存在`。

```bash
curl -H "X-User-Id: u1001" -o avatar.mp4 \
  http://36.136.54.165:8888/QeMixAvatar/api/characters/<形象ID>/video
```

---

## 6. 作品接口

作品对象字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 作品 ID |
| `task_name` | string | 作品名称 |
| `username` / `user_id` | string | 提交用户，两者相同 |
| `avatar_identifier` | string | 形象 ID |
| `avatar_name` | string | 形象名称 |
| `avatar_thumbnail` | string | 形象封面相对路径 |
| `avatar_preview_video` / `avatar_video_path` | string | 形象预览视频相对路径 |
| `avatar_bake_status` | string | 形象转码状态 |
| `status` | string | `wait` / `run` / `done` / `cancelled` / `error` |
| `progress` | number | 0～100 |
| `progress_message` | string | 进度说明；已取消为 `已取消`，合成失败时为 `合成失败` |
| `error_message` | string | 仅 `status=error` 时的失败原因；已取消时为空 |
| `result_path` / `result_path_lbr` | string | 完成后的预览地址，未完成时为空。值为 `/api/tasks/{task_id}/preview` |
| `result_thumbnail` | string | 完成后的封面（当前为形象封面） |
| `steps` | int | 合成步数 |
| `quality_label` | string | `标准` / `高质量` / `超高质量` |
| `audio_name` | string | 音频文件名 |
| `audio_duration` | number | 音频时长（秒） |
| `remaining_seconds` | number | 仅 `status=run` 时为预计剩余秒数；排队中为 `null` |
| `total_duration_text` | string | 仅合成中给出剩余时间文案，如「约 12 分钟」；排队中为空 |
| `created_at` / `started_at` / `finished_at` | string | 时间 |

原片地址固定为 `/api/tasks/{task_id}/download`，不在 JSON 里单独给出。

### 6.1 分页列出作品

`GET /api/tasks`

**请始终传 `username` 或 `user_id`**，只取该用户的作品。不传用户 ID 时，不会返回使用了个人形象的作品。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `page` | int | 否 | 默认 `1` |
| `page_size` | int | 否 | 默认 `12`，最大 `100` |
| `username` 或 `user_id` | string | 强烈建议 | 按提交用户筛选 |
| `status` | string | 否 | `wait` / `run` / `done` / `cancelled` / `error`（也接受 `queued` / `running` / `failed`；`active` 表示排队中+合成中） |
| `keyword` | string | 否 | 搜索作品名、音频名、形象名 |

成功 `data`：

```json
{
  "tasks": [ { "task_id": "f1e2d3c4b5a697887766554433221100" } ],
  "total": 1,
  "page": 1,
  "pages": 1,
  "page_size": 12
}
```

`tasks` 元素为完整作品对象。

```bash
curl -s "http://36.136.54.165:8888/QeMixAvatar/api/tasks?username=u1001&status=wait&page=1"
```

---

### 6.2 创建合成作品

`POST /api/tasks/create`  
`Content-Type: multipart/form-data`

音频一次性上传（不必再走形象那种分片）。形象必须 `bake_status=ready`。使用个人形象时，`username` 必须是该形象所属用户。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `avatar_identifier` | string | 是 | 形象 ID |
| `username` | string | 是 | 用户 ID（公共形象也要填） |
| `task_name` | string | 是 | 作品名称 |
| `audio` | file | 是 | 驱动音频 |
| `steps` | int | 否 | 默认 `30`，仅 `30` / `50` / `80` |

成功 `msg`：`已加入合成队列`，`data` 为作品对象，此时 `status` 为 `wait`。

| `msg` | 场景 |
|---|---|
| `请填写作品名称` | `task_name` 为空 |
| `请填写用户ID` | `username` 为空 |
| `用户ID无效` / `用户ID不能超过 64 个字符` | 用户 ID 非法 |
| `步数只能是 30, 50, 80` | `steps` 非法 |
| `不支持的音频格式：.xxx` | 后缀不支持 |
| `形象不存在` | ID 无效，或个人形象不属于该用户 |
| `形象还在转码，请稍后再提交` | 形象未就绪 |
| `音频文件为空` | 文件空 |
| `音频不能超过 300MB` | 超限 |

```bash
curl -s -X POST http://36.136.54.165:8888/QeMixAvatar/api/tasks/create \
  -F avatar_identifier=<形象ID> \
  -F username=u1001 \
  -F task_name=今日新闻 \
  -F steps=30 \
  -F audio=@speech.wav
```

---

### 6.3 查询作品（轮询）

`GET /api/tasks/{task_id}`

个人形象作品必须带 Query `username` 或 `user_id`，否则视为不存在。建议一律带上。

成功时 `data` 为单个作品对象。请轮询 `status`、`progress`。

完成后 `status=done`，`result_path` 为 `/api/tasks/{task_id}/preview`。

失败 `msg`：`任务不存在`。

```bash
curl -s "http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>?username=u1001"
```

建议间隔 2～5 秒轮询，`status` 为 `done`、`error` 或 `cancelled` 后停止。

---

### 6.4 取消合成

`POST /api/tasks/{task_id}/cancel`

个人作品请带 Query `username` 或 `user_id`。`status=wait`（排队）或 `status=run`（合成中）可取消。已经是 `cancelled` 再调用一次仍返回 `已取消`。

成功后该条立刻变为 `cancelled`，`progress_message` 为 `已取消`，`error_message` 为空，`msg` 为 `已取消`，可以再调用重试。合成中取消后 GPU 会空出，队列里下一条自动开始，不必再轮询这条任务。已取消**不是**失败，`status` 不会是 `error`。

已完成、已失败的任务不能取消；合成中请用本接口，不要直接删除。

| `msg` | 场景 |
|---|---|
| `已取消` | 排队中或合成中的任务已取消 |
| `任务不存在` | ID 无效或不属于该用户 |
| `已完成的任务不能取消` | `status=done` |
| `已结束的任务不能取消` | `status=error` |

```bash
curl -s -X POST "http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>/cancel?username=u1001"
```

---

### 6.5 重试合成

`POST /api/tasks/{task_id}/retry`

个人作品请带 Query `username` 或 `user_id`。合成中（`status=run`）不能重试。`status=error`（合成失败）或 `status=cancelled`（已取消）后都可重试。音频文件必须仍在。成功后重新进入排队，`msg` 为 `已重新排队`。

| `msg` | 场景 |
|---|---|
| `任务不存在` | ID 无效或不属于该用户 |
| `正在合成的任务不能重试` | `status=run` |
| `音频文件已丢失，无法重试` | 音频不在 |

```bash
curl -s -X POST "http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>/retry?username=u1001"
```

---

### 6.6 预览成片（低码率）

`GET /api/tasks/{task_id}/preview`

使用了**个人形象**的作品必须带请求头 `X-User-Id`。裸 URL、分享链接、`?user_id=` 均无法打开。

成功：HTTP `200`，`Content-Type: video/mp4`。原分辨率、约 2Mbps、**带声音**（AAC）。个人成片带 `Cache-Control: private, no-store`。无损原片请用 6.7 下载。

| HTTP | `detail` | 场景 |
|---|---|---|
| 404 | `任务不存在` | ID 无效，或个人成片未带合法身份头 |
| 400 | `任务尚未合成完成` | 未完成 |
| 404 | `成片文件不存在` | 记录完成但文件丢失 |

```bash
# 个人成片
curl -H "X-User-Id: u1001" -o preview.mp4 \
  http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>/preview

# 以下都拿不到个人成片
curl -o preview.mp4 http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>/preview
curl -o preview.mp4 "http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>/preview?user_id=u1001"
```

---

### 6.7 下载成片（原片）

`GET /api/tasks/{task_id}/download`

鉴权、失败情况与 6.6 相同。成功时另有：

```http
Content-Disposition: attachment; filename="{task_id}.mp4"
```

```bash
curl -H "X-User-Id: u1001" -OJ \
  http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>/download
```

---

### 6.8 删除作品

`DELETE /api/tasks/{task_id}`

个人作品请带 Query `username` 或 `user_id`。合成中不能删除，请先取消。

成功 `msg`：`已删除`。

| `msg` | 场景 |
|---|---|
| `任务不存在` | ID 无效或不属于该用户 |
| `正在合成的任务不能删除` | `status=run` |

```bash
curl -s -X DELETE "http://36.136.54.165:8888/QeMixAvatar/api/tasks/<作品ID>?username=u1001"
```

---

## 7. 对接注意

1. **只使用本文列出的路径。** 其它路径不在对接范围内。
2. 公网 Base URL 必须是 `http://36.136.54.165:8888/QeMixAvatar`。写成 `:8811` 会 Connection refused；漏掉 `/QeMixAvatar` 会 404。
3. 用户 ID 由你们系统生成并保管；本接口按该 ID 隔离个人库，不会替你们做登录态。
4. 个人媒体必须由你们的服务端加 `X-User-Id` 拉取，不要把可打开的文件地址交给最终用户去分享。
5. `identifier` 与 `id`、`username` 与 `user_id`、`thumbnail` 与 `preview_thumbnail`、`video_path` 与 `preview_video_path`、`result_path` 与 `result_path_lbr` 为同一数据的别名，任意取一个即可。
6. 相对路径拼在 Base URL 后，不要再自己加一层前缀。
7. 批量上传请按文件并行调用 4.2，不要自行拼接多文件 multipart。中途放弃必须 `abort`；未完成的分片超过 24 小时无活动会被服务端删除。
