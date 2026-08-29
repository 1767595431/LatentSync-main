# 数字人 API 接口文档

Base URL：`http://<主机>:8765`

---

## 1. 通用约定

### 1.1 统一响应

形象（`/api/avatars`）与作品（`/api/tasks`）接口使用统一信封。失败时 HTTP 状态码仍为 `200`。

正确响应：

```json
{
  "code": 0,
  "msg": "ok",
  "success": true,
  "data": {}
}
```

失败响应：

```json
{
  "code": 1,
  "msg": "错误说明",
  "success": false,
  "data": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | int | `0` 成功，非 `0` 失败 |
| `msg` | string | 提示信息 |
| `success` | bool | 是否成功 |
| `data` | object / null | 成功为业务数据，失败为 `null` |

`/api/uploads`、`/api/characters`、`/api/jobs` 不走统一信封：成功直接返回业务 JSON；失败为 HTTP `4xx`，正文：

```json
{ "detail": "错误说明" }
```

### 1.2 形象类型

| 取值 | 说明 |
|---|---|
| `public` | 公共形象，不需要用户 ID |
| `private` | 个人形象，必须传 `user_id` 或 `username` |

`username` 与 `user_id` 表示同一用户标识，最长 64 个字符。

### 1.3 形象状态

| `status` | `bake_status` | 说明 |
|---|---|---|
| `queued` / `preparing` | `processing` | 转码中 |
| `ready` | `ready` | 已就绪，可创建作品 |
| `failed` | `error` | 转码失败 |

### 1.4 作品状态

| 内部状态 | 作品接口 `status` | 说明 |
|---|---|---|
| `queued` | `wait` | 排队中 |
| `running` | `run` | 合成中 |
| `done` | `done` | 已完成 |
| `failed` | `error` | 失败 |

### 1.5 合成步数

| `steps` | `quality_label` |
|---|---|
| `20` | 标准（默认） |
| `30` | 标准+ |
| `40` | 清晰 |
| `50` | 高质量 |
| `60` | 高质量+ |
| `70` | 精细 |
| `80` | 超高质量 |

只接受以上取值。

### 1.6 素材限制

| 种类 | 支持后缀 | 大小上限 |
|---|---|---|
| 形象视频 | `.mp4` `.mov` `.mkv` `.webm` `.avi` | 2GB |
| 驱动音频 | `.wav` `.mp3` `.m4a` `.aac` `.flac` `.ogg` | 300MB |

时间字段为 UTC，格式 `YYYY-MM-DDTHH:MM:SSZ`。

---

## 2. 形象接口

### 2.1 分页列出形象

`GET /api/avatars`

**请求参数（Query）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | 否 | `public` / `private` / `all` |
| `username` | string | 否 | 按用户筛选个人形象，可与 `user_id` 互换 |
| `user_id` | string | 否 | 同 `username` |
| `bake_status` | string | 否 | `ready` / `processing` / `error` / `missing` |
| `page` | int | 否 | 页码，从 1 起，默认 `1` |
| `page_size` | int | 否 | 每页条数，默认 `12`，最大 `100` |

**正确响应**

```json
{
  "code": 0,
  "msg": "ok",
  "success": true,
  "data": {
    "items": [
      {
        "identifier": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
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
        "thumbnail": "/api/characters/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/poster",
        "preview_thumbnail": "/api/characters/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/poster",
        "video_path": "/api/characters/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/video",
        "preview_video_path": "/api/characters/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/video",
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
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | array | 形象列表 |
| `identifier` / `id` | string | 形象 ID |
| `name` | string | 名称 |
| `type` | string | `public` / `private` |
| `user_id` / `username` | string / null | 个人形象所属用户 |
| `status` | string | 内部状态 |
| `bake_status` | string | 对外状态 |
| `bake_message` | string | 转码进度或失败原因 |
| `thumbnail` | string | 封面地址，未生成时为空 |
| `video_path` / `preview_video_path` | string | 转码完成后的视频地址 |
| `duration` / `width` / `height` | number / null | 素材信息 |
| `created_at` | string | 创建时间 |
| `counts` | object | 公共/个人形象总数 |
| `ready_counts` | object | 已就绪数量 |

**失败响应**

```json
{
  "code": 1,
  "msg": "形象类型只能是 public（公共）或 private（个人）",
  "success": false,
  "data": null
}
```

**调用示例**

```bash
curl -s "http://127.0.0.1:8765/api/avatars?type=public&page=1&page_size=12"
```

---

### 2.2 分片上传形象

`POST /api/avatars/upload`  
`Content-Type: multipart/form-data`

分三个阶段调用，由 `stage` 区分：`init` → `chunk` → `complete`。

#### 2.2.1 初始化 `stage=init`

**请求参数（Form）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `stage` | string | 是 | 固定 `init` |
| `name` | string | 是 | 形象名称 |
| `type` | string | 否 | 默认 `public` |
| `username` / `user_id` | string | 个人必填 | 用户 ID |
| `filename` | string | 是 | 原文件名，用于判断后缀 |
| `filesize` | int | 是 | 文件字节数 |
| `chunk_size` | int | 否 | 分片大小，64KB～16MB，默认 2MB |

**正确响应**

```json
{
  "code": 0,
  "msg": "ok",
  "success": true,
  "data": {
    "upload_id": "7c9e6679b3d84c3e8f1a2b3c4d5e6f70",
    "total_chunks": 5,
    "chunk_size": 8388608
  }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `请填写形象名称` | `name` 为空 |
| `个人形象必须填写用户ID` | `type=private` 且未传用户 ID |
| `用户ID不能超过 64 个字符` | 用户 ID 超长 |
| `不支持的视频格式：.xxx` | 后缀不在允许列表 |
| `视频大小需在 1B–2GB 之间` | `filesize` 非法 |
| `分片大小无效` | `chunk_size` 越界 |

```json
{
  "code": 1,
  "msg": "个人形象必须填写用户ID",
  "success": false,
  "data": null
}
```

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/avatars/upload \
  -F stage=init \
  -F name=新闻主播 \
  -F type=public \
  -F filename=host.mp4 \
  -F filesize=$(stat -c%s host.mp4) \
  -F chunk_size=8388608
```

个人形象增加：`-F type=private -F username=u1001`

#### 2.2.2 上传分片 `stage=chunk`

**请求参数（Form）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `stage` | string | 是 | 固定 `chunk` |
| `upload_id` | string | 是 | 初始化返回的 ID |
| `chunk_index` | int | 是 | 分片序号，从 `0` 起 |
| `total_chunks` | int | 否 | 总分片数 |
| `chunk` | file | 是 | 本片二进制 |

最后一片大小必须等于 `filesize - chunk_size * (total_chunks - 1)`，其余片必须正好等于 `chunk_size`。

**正确响应**

```json
{
  "code": 0,
  "msg": "ok",
  "success": true,
  "data": {
    "index": 0,
    "received": 1,
    "total_chunks": 5
  }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `分片参数不完整` | 缺少 `upload_id` / `chunk` / `chunk_index` |
| `上传任务不存在` | `upload_id` 无效 |
| `分片序号无效` | `chunk_index` 越界 |
| `空分片` | 分片内容为空 |
| `分片大小应为 N 字节` | 非最后一片大小不对 |
| `最后分片大小应为 N 字节` | 最后一片大小不对 |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/avatars/upload \
  -F stage=chunk \
  -F upload_id=<上传ID> \
  -F chunk_index=0 \
  -F total_chunks=<总分片> \
  -F chunk=@chunk0.bin
```

#### 2.2.3 完成上传 `stage=complete`

**请求参数（Form）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `stage` | string | 是 | 固定 `complete` |
| `upload_id` | string | 是 | 初始化返回的 ID |

成功后形象进入转码队列，`data` 为形象对象（字段同 2.1）。

**正确响应**

```json
{
  "code": 0,
  "msg": "上传成功，正在处理",
  "success": true,
  "data": {
    "identifier": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    "name": "新闻主播",
    "type": "public",
    "user_id": null,
    "username": null,
    "status": "queued",
    "bake_status": "processing",
    "bake_progress": 0,
    "bake_message": "",
    "thumbnail": "",
    "video_path": "",
    "preview_video_path": "",
    "created_at": "2026-08-30T01:20:10Z"
  }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `缺少 upload_id` | 未传 `upload_id` |
| `上传任务不存在` | `upload_id` 无效 |
| `分片不完整` | 未收齐全部分片 |
| `分片 N 文件丢失` | 分片文件缺失 |
| `合并后文件大小与声明不一致` | 合并结果与 `filesize` 不符 |
| `未知的上传阶段` | `stage` 不是 `init` / `chunk` / `complete` |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/avatars/upload \
  -F stage=complete \
  -F upload_id=<上传ID>
```

转码完成后 `bake_status` 变为 `ready`，可用 2.1 轮询。

---

### 2.3 重新转码形象

`POST /api/avatars/{identifier}/rebake`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `identifier` | string | 是 | 形象 ID |

**正确响应**

```json
{
  "code": 0,
  "msg": "已重新排队转码",
  "success": true,
  "data": { "identifier": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4" }
}
```

**失败响应**

```json
{
  "code": 1,
  "msg": "形象不存在",
  "success": false,
  "data": null
}
```

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/avatars/<形象ID>/rebake
```

---

### 2.4 删除形象

`DELETE /api/avatars/{identifier}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `identifier` | string | 是 | 形象 ID |

该形象若存在排队或合成中的作品，不能删除。

**正确响应**

```json
{
  "code": 0,
  "msg": "已删除",
  "success": true,
  "data": { "identifier": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4" }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `形象不存在` | ID 无效 |
| `该形象还有排队或正在合成的任务` | 仍有未完成作品 |

**调用示例**

```bash
curl -s -X DELETE http://127.0.0.1:8765/api/avatars/<形象ID>
```

---

## 3. 作品接口

作品对象字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 作品 ID |
| `task_name` | string | 作品名称 |
| `username` / `user_id` | string | 提交用户 |
| `avatar_identifier` | string | 形象 ID |
| `avatar_name` | string | 形象名称 |
| `avatar_thumbnail` | string | 形象封面 |
| `status` | string | `wait` / `run` / `done` / `error` |
| `progress` | number | 0～100 |
| `progress_message` | string | 进度说明 |
| `error_message` | string | 失败原因 |
| `result_path` | string | 完成后的预览地址，未完成时为空 |
| `steps` | int | 合成步数 |
| `quality_label` | string | 质量档名称 |
| `audio_name` | string | 音频文件名 |
| `audio_duration` | number | 音频时长（秒） |
| `remaining_seconds` | number | 预计剩余秒数 |
| `total_duration_text` | string | 剩余时间文案，如「约 12 分钟」 |
| `created_at` / `started_at` / `finished_at` | string | 时间 |

### 3.1 分页列出作品

`GET /api/tasks`

**请求参数（Query）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `page` | int | 否 | 页码，从 1 起，默认 `1` |
| `page_size` | int | 否 | 每页条数，默认 `12`，最大 `100` |
| `username` / `user_id` | string | 否 | 按用户筛选 |
| `status` | string | 否 | `wait` / `run` / `done` / `error`，也接受 `queued` / `running` / `failed` |
| `keyword` | string | 否 | 搜索作品名、音频名、形象名 |

**正确响应**

```json
{
  "code": 0,
  "msg": "ok",
  "success": true,
  "data": {
    "tasks": [
      {
        "task_id": "f1e2d3c4b5a697887766554433221100",
        "task_name": "今日新闻",
        "username": "u1001",
        "user_id": "u1001",
        "avatar_identifier": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "avatar_name": "新闻主播",
        "avatar_thumbnail": "/api/characters/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/poster",
        "status": "wait",
        "progress": 0,
        "progress_message": "排队中",
        "error_message": "",
        "result_path": "",
        "steps": 20,
        "quality_label": "标准",
        "audio_name": "speech.wav",
        "audio_duration": 12.5,
        "remaining_seconds": 180,
        "total_duration_text": "约 3 分钟",
        "created_at": "2026-08-30T01:30:00Z",
        "started_at": null,
        "finished_at": null
      }
    ],
    "total": 1,
    "page": 1,
    "pages": 1,
    "page_size": 12
  }
}
```

**调用示例**

```bash
curl -s "http://127.0.0.1:8765/api/tasks?username=u1001&status=wait&page=1"
```

---

### 3.2 创建合成作品

`POST /api/tasks/create`  
`Content-Type: multipart/form-data`

**请求参数（Form）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `avatar_identifier` | string | 是 | 已就绪的形象 ID |
| `username` | string | 是 | 用户 ID（公共形象也要填） |
| `task_name` | string | 是 | 作品名称 |
| `audio` | file | 是 | 驱动音频 |
| `steps` | int | 否 | 默认 `20` |

形象必须已转码完成（`bake_status=ready`）。

**正确响应**

```json
{
  "code": 0,
  "msg": "已加入合成队列",
  "success": true,
  "data": {
    "task_id": "f1e2d3c4b5a697887766554433221100",
    "task_name": "今日新闻",
    "username": "u1001",
    "user_id": "u1001",
    "avatar_identifier": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    "avatar_name": "新闻主播",
    "status": "wait",
    "progress": 0,
    "progress_message": "排队中",
    "steps": 20,
    "quality_label": "标准",
    "audio_name": "speech.wav",
    "result_path": ""
  }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `请填写作品名称` | `task_name` 为空 |
| `请填写用户ID` | `username` 为空 |
| `步数只能是 20, 30, 40, 50, 60, 70, 80` | `steps` 非法 |
| `不支持的音频格式：.xxx` | 音频后缀不支持 |
| `形象不存在` | 形象 ID 无效 |
| `形象还在转码，请稍后再提交` | 形象未就绪 |
| `音频文件为空` | 音频内容为空 |
| `音频不能超过 300MB` | 超过大小上限 |

```json
{
  "code": 1,
  "msg": "形象还在转码，请稍后再提交",
  "success": false,
  "data": null
}
```

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/tasks/create \
  -F avatar_identifier=<形象ID> \
  -F username=u1001 \
  -F task_name=今日新闻 \
  -F steps=20 \
  -F audio=@speech.wav
```

---

### 3.3 查询作品

`GET /api/tasks/{task_id}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 作品 ID |

`data` 为单个作品对象，可用于轮询 `status`、`progress`。

**正确响应**

```json
{
  "code": 0,
  "msg": "ok",
  "success": true,
  "data": {
    "task_id": "f1e2d3c4b5a697887766554433221100",
    "task_name": "今日新闻",
    "status": "run",
    "progress": 35,
    "progress_message": "合成中 3/20 · 15%",
    "remaining_seconds": 120,
    "total_duration_text": "约 2 分钟",
    "result_path": ""
  }
}
```

完成后 `status` 为 `done`，`result_path` 为 `/api/tasks/{task_id}/preview`。

**失败响应**

```json
{
  "code": 1,
  "msg": "任务不存在",
  "success": false,
  "data": null
}
```

**调用示例**

```bash
curl -s http://127.0.0.1:8765/api/tasks/<作品ID>
```

---

### 3.4 重试合成

`POST /api/tasks/{task_id}/retry`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 作品 ID |

合成中的作品不能重试。音频文件必须仍在。成功后重新进入排队。

**正确响应**

```json
{
  "code": 0,
  "msg": "已重新排队",
  "success": true,
  "data": { "task_id": "f1e2d3c4b5a697887766554433221100" }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `任务不存在` | ID 无效 |
| `正在合成的任务不能重试` | `status=run` |
| `音频文件已丢失，无法重试` | 音频文件不存在 |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/tasks/<作品ID>/retry
```

---

### 3.5 预览成片

`GET /api/tasks/{task_id}/preview`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 作品 ID |

**正确响应**

HTTP `200`，`Content-Type: video/mp4`，正文为成片二进制。

**失败响应**

```json
{
  "code": 1,
  "msg": "任务尚未合成完成",
  "success": false,
  "data": null
}
```

| `msg` | 场景 |
|---|---|
| `任务不存在` | ID 无效 |
| `任务尚未合成完成` | 未完成或无成片 |
| `成片文件不存在` | 记录完成但文件丢失 |

**调用示例**

```bash
curl -s -o result.mp4 http://127.0.0.1:8765/api/tasks/<作品ID>/preview
```

---

### 3.6 下载成片

`GET /api/tasks/{task_id}/download`

参数、正确/失败响应与 3.5 相同。成功时带附件头：

`Content-Disposition: attachment; filename="{task_id}.mp4"`

**调用示例**

```bash
curl -OJ http://127.0.0.1:8765/api/tasks/<作品ID>/download
```

---

### 3.7 删除作品

`DELETE /api/tasks/{task_id}`

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 作品 ID |

合成中的作品不能删除。

**正确响应**

```json
{
  "code": 0,
  "msg": "已删除",
  "success": true,
  "data": { "task_id": "f1e2d3c4b5a697887766554433221100" }
}
```

**失败响应**

| `msg` | 场景 |
|---|---|
| `任务不存在` | ID 无效 |
| `正在合成的任务不能删除` | `status=run` |

**调用示例**

```bash
curl -s -X DELETE http://127.0.0.1:8765/api/tasks/<作品ID>
```

---

## 4. JSON 形象接口

本组接口成功直接返回业务 JSON，失败为 HTTP `4xx` + `{ "detail": "错误说明" }`。

### 4.1 列出形象

`GET /api/characters`

**请求参数（Query）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | 否 | `public` / `private`，不传则全部返回 |
| `user_id` / `username` | string | 否 | 按用户筛选个人形象 |

**正确响应**

```json
[
  {
    "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    "name": "新闻主播",
    "status": "ready",
    "type": "public",
    "user_id": null,
    "video_path": "/root/.../characters/.../video.mp4",
    "poster_path": "/root/.../characters/.../poster.jpg",
    "duration": 8.32,
    "width": 1920,
    "height": 1080,
    "created_at": "2026-08-30T01:20:00Z"
  }
]
```

**失败响应**

HTTP `400`

```json
{ "detail": "形象类型只能是 public（公共）或 private（个人）" }
```

**调用示例**

```bash
curl -s "http://127.0.0.1:8765/api/characters?type=private&user_id=u1001"
```

---

### 4.2 创建形象

`POST /api/characters`  
`Content-Type: application/json`

须先用第 6 节完成视频分片上传，再传入 `video_upload_id`。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 形象名称 |
| `video_upload_id` | string | 是 | 已完成的视频上传 ID |
| `type` | string | 否 | 默认 `public` |
| `user_id` / `username` | string | 个人必填 | 用户 ID |

```json
{
  "name": "新闻主播",
  "video_upload_id": "7c9e6679b3d84c3e8f1a2b3c4d5e6f70",
  "type": "private",
  "user_id": "u1001"
}
```

**正确响应**

```json
{
  "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "name": "新闻主播",
  "status": "queued",
  "type": "private",
  "user_id": "u1001"
}
```

**失败响应**

| HTTP | `detail` | 场景 |
|---|---|---|
| 400 | `请填写形象名称` | `name` 为空 |
| 400 | `个人形象必须填写用户ID` | 个人形象未传用户 ID |
| 400 | `视频上传不存在` | `video_upload_id` 无效 |
| 400 | `请先完成视频分片上传` | 上传未完成或类型不是视频 |
| 400 | `视频文件不存在` | 上传文件丢失 |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/characters \
  -H "Content-Type: application/json" \
  -d '{"name":"新闻主播","video_upload_id":"<上传ID>","type":"public"}'
```

---

### 4.3 查询形象

`GET /api/characters/{character_id}`

**正确响应**：单个形象对象，字段同 4.1。

**失败响应**

HTTP `404`

```json
{ "detail": "形象不存在" }
```

**调用示例**

```bash
curl -s http://127.0.0.1:8765/api/characters/<形象ID>
```

---

### 4.4 删除形象

`DELETE /api/characters/{character_id}`

该形象若存在排队或合成中的作品，不能删除。

**正确响应**

```json
{ "ok": true }
```

**失败响应**

| HTTP | `detail` |
|---|---|
| 400 | `该形象还有排队或正在合成的任务` |
| 404 | `形象不存在` |

**调用示例**

```bash
curl -s -X DELETE http://127.0.0.1:8765/api/characters/<形象ID>
```

---

### 4.5 获取形象封面

`GET /api/characters/{character_id}/poster`

**正确响应**：HTTP `200`，`Content-Type: image/jpeg`。

**失败响应**

HTTP `404`

```json
{ "detail": "暂无封面" }
```

---

### 4.6 获取形象视频

`GET /api/characters/{character_id}/video`

**正确响应**：HTTP `200`，`Content-Type: video/mp4`。

**失败响应**

HTTP `404`

```json
{ "detail": "形象视频不存在" }
```

---

## 5. JSON 作品接口

本组接口成功直接返回业务 JSON，失败为 HTTP `4xx` + `{ "detail": "错误说明" }`。状态值为内部值：`queued` / `running` / `done` / `failed`。

### 5.1 列出合成任务

`GET /api/jobs`

**正确响应**

```json
[
  {
    "id": "f1e2d3c4b5a697887766554433221100",
    "character_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    "character_name": "新闻主播",
    "status": "queued",
    "progress": "排队中",
    "steps": 20,
    "username": "u1001",
    "task_name": "今日新闻",
    "audio_name": "speech.wav",
    "audio_duration": 12.5,
    "created_at": "2026-08-30T01:30:00Z"
  }
]
```

**调用示例**

```bash
curl -s http://127.0.0.1:8765/api/jobs
```

---

### 5.2 提交合成任务

`POST /api/jobs`  
`Content-Type: application/json`

须先用第 6 节完成音频分片上传，再传入 `audio_upload_id`。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `character_id` | string | 是 | 已就绪的形象 ID |
| `audio_upload_id` | string | 是 | 已完成的音频上传 ID |
| `steps` | int | 否 | 默认 `20` |
| `username` | string | 否 | 用户 ID |
| `task_name` | string | 否 | 作品名称 |

```json
{
  "character_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "audio_upload_id": "9a8b7c6d5e4f3210aabbccddeeff0011",
  "steps": 20,
  "username": "u1001",
  "task_name": "今日新闻"
}
```

**正确响应**：单个任务对象，字段同 5.1，`status` 为 `queued`。

**失败响应**

| HTTP | `detail` | 场景 |
|---|---|---|
| 400 | `步数只能是 20, 30, 40, 50, 60, 70, 80` | `steps` 非法 |
| 400 | `形象不存在` | 形象 ID 无效 |
| 400 | `形象还在转码，请稍后再提交` | 形象未就绪 |
| 400 | `音频上传不存在` | `audio_upload_id` 无效 |
| 400 | `请先完成音频分片上传` | 上传未完成或类型不是音频 |
| 400 | `音频文件不存在` | 上传文件丢失 |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"character_id":"<形象ID>","audio_upload_id":"<音频上传ID>","steps":20,"username":"u1001","task_name":"今日新闻"}'
```

---

### 5.3 查询合成任务

`GET /api/jobs/{job_id}`

**正确响应**：单个任务对象。

**失败响应**

HTTP `404`

```json
{ "detail": "任务不存在" }
```

**调用示例**

```bash
curl -s http://127.0.0.1:8765/api/jobs/<任务ID>
```

---

### 5.4 预览成片

`GET /api/jobs/{job_id}/preview`

**正确响应**：HTTP `200`，`Content-Type: video/mp4`。

**失败响应**

| HTTP | `detail` |
|---|---|
| 400 | `任务尚未合成完成` |
| 404 | `任务不存在` |
| 404 | `成片文件不存在` |

---

### 5.5 下载成片

`GET /api/jobs/{job_id}/download`

与 5.4 相同，成功时带附件头 `Content-Disposition: attachment; filename="{job_id}.mp4"`。

---

### 5.6 删除合成任务

`DELETE /api/jobs/{job_id}`

合成中的任务不能删除。

**正确响应**

```json
{ "ok": true }
```

**失败响应**

| HTTP | `detail` |
|---|---|
| 400 | `正在合成的任务不能删除` |
| 404 | `任务不存在` |

**调用示例**

```bash
curl -s -X DELETE http://127.0.0.1:8765/api/jobs/<任务ID>
```

---

## 6. 通用分片上传

给第 4、5 节使用。分片大小固定 **2MB**（`2097152` 字节）。成功直接返回业务 JSON，失败为 HTTP `4xx` + `{ "detail": "错误说明" }`。

### 6.1 创建分片上传

`POST /api/uploads`  
`Content-Type: application/json`

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filename` | string | 是 | 原文件名 |
| `size` | int | 是 | 文件字节数 |
| `kind` | string | 是 | `video` 或 `audio` |
| `mime` | string | 否 | MIME 类型 |

```json
{
  "filename": "host.mp4",
  "size": 10485760,
  "mime": "video/mp4",
  "kind": "video"
}
```

**正确响应**

```json
{
  "upload_id": "7c9e6679b3d84c3e8f1a2b3c4d5e6f70",
  "chunk_size": 2097152,
  "total_chunks": 5,
  "received": []
}
```

**失败响应**

| HTTP | `detail` | 场景 |
|---|---|---|
| 400 | `不支持的视频格式：.xxx` | `kind=video` 且后缀不支持 |
| 400 | `视频大小需在 1B–2GB 之间` | 视频大小非法 |
| 400 | `不支持的音频格式：.xxx` | `kind=audio` 且后缀不支持 |
| 400 | `音频大小需在 1B–300MB 之间` | 音频大小非法 |
| 422 | 校验失败 | `kind` 不是 `video` / `audio` |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/uploads \
  -H "Content-Type: application/json" \
  -d '{"filename":"host.mp4","size":10485760,"kind":"video"}'
```

---

### 6.2 查询分片上传

`GET /api/uploads/{upload_id}`

**正确响应**

```json
{
  "id": "7c9e6679b3d84c3e8f1a2b3c4d5e6f70",
  "kind": "video",
  "filename": "host.mp4",
  "size": 10485760,
  "chunk_size": 2097152,
  "total_chunks": 5,
  "received": [0, 1],
  "status": "uploading",
  "path": null,
  "created_at": "2026-08-30T01:18:00Z"
}
```

`status`：`uploading` 上传中，`ready` 已合并。

**失败响应**

HTTP `404`

```json
{ "detail": "上传任务不存在" }
```

---

### 6.3 上传一个分片

`PUT /api/uploads/{upload_id}/chunks/{index}`  
`Content-Type: application/octet-stream`

请求体为原始二进制。`index` 从 `0` 起。非最后一片必须正好 `chunk_size` 字节，最后一片必须等于剩余字节数。

**路径参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `upload_id` | string | 是 | 上传 ID |
| `index` | int | 是 | 分片序号，从 `0` 起 |

**正确响应**

```json
{
  "ok": true,
  "index": 0,
  "received": 1,
  "total_chunks": 5
}
```

**失败响应**

| HTTP | `detail` |
|---|---|
| 404 | `上传任务不存在` |
| 400 | `该上传已结束` |
| 400 | `分片序号无效` |
| 400 | `空分片` |
| 400 | `分片过大` |
| 400 | `分片大小应为 N 字节` |
| 400 | `最后分片大小应为 N 字节` |

**调用示例**

```bash
curl -s -X PUT http://127.0.0.1:8765/api/uploads/<上传ID>/chunks/0 \
  --data-binary @chunk0.bin
```

---

### 6.4 合并分片文件

`POST /api/uploads/{upload_id}/complete`

全部到齐后才能合并。

**正确响应**

```json
{
  "ok": true,
  "upload_id": "7c9e6679b3d84c3e8f1a2b3c4d5e6f70",
  "path": "/root/.../uploads/7c9e6679b3d84c3e8f1a2b3c4d5e6f70/host.mp4",
  "size": 10485760
}
```

**失败响应**

| HTTP | `detail` |
|---|---|
| 404 | `上传任务不存在` |
| 400 | `分片不完整，缺少 N 片` |
| 400 | `分片 N 文件丢失` |
| 400 | `合并后文件大小与声明不一致` |

**调用示例**

```bash
curl -s -X POST http://127.0.0.1:8765/api/uploads/<上传ID>/complete
```
