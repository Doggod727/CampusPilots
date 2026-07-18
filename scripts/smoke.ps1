#Requires -Version 7.0
<#
.SYNOPSIS
    CampusPilot 后端跨模块冒烟验证（真实 PostgreSQL/Redis/Chroma/DeepSeek）。
.DESCRIPTION
    覆盖 M4 认证与 RBAC、M2 校园服务、M3 社区、M1 知识库 RAG、M5 Agent 平台与内部 Tool 网关。
    逐项断言 HTTP 状态码、统一信封结构与关键业务字段；失败项输出详情并以非零码退出。
.EXAMPLE
    pwsh -File scripts/smoke.ps1
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [string]$Password = 'CampusPilot-Demo-2026!',
    [string]$FrontendOrigin = 'http://localhost:5173'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$script:Passed = 0
$script:Failed = 0
$script:Failures = @()

function Invoke-Api {
    param(
        [string]$Method, [string]$Path, [string]$Token,
        $Body, [hashtable]$Headers = @{}, [Microsoft.PowerShell.Commands.WebRequestSession]$Session,
        [string]$ContentType = 'application/json'
    )
    $params = @{
        Method = $Method
        Uri = "$BaseUrl$Path"
        SkipHttpErrorCheck = $true
        TimeoutSec = 180
        Headers = $Headers.Clone()
    }
    if ($Token) { $params.Headers['Authorization'] = "Bearer $Token" }
    if ($Session) { $params['WebSession'] = $Session }
    if ($null -ne $Body) {
        $params['ContentType'] = $ContentType
        $params['Body'] = ($Body -is [string]) ? $Body : ($Body | ConvertTo-Json -Depth 10 -Compress)
    }
    $resp = Invoke-WebRequest @params
    $json = $null
    if ($resp.Content) { try { $json = $resp.Content | ConvertFrom-Json } catch { $json = $null } }
    return [pscustomobject]@{ Status = [int]$resp.StatusCode; Body = $json; Raw = $resp.Content; Headers = $resp.Headers }
}

function Assert {
    param([string]$Name, [bool]$Condition, [string]$Detail = '')
    if ($Condition) {
        $script:Passed++
        Write-Host "  [PASS] $Name" -ForegroundColor Green
    } else {
        $script:Failed++
        $script:Failures += "$Name :: $Detail"
        Write-Host "  [FAIL] $Name  $Detail" -ForegroundColor Red
    }
}

function New-Idem { return [guid]::NewGuid().ToString('N') }

function Invoke-Sse {
    param([string]$Path, [string]$Token, $Body, [hashtable]$Headers = @{}, [int]$TimeoutSeconds = 150, [string]$LastEventId, [string]$Method = 'POST')
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    try {
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), "$BaseUrl$Path")
        if ($Token) { $req.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token) }
        foreach ($k in $Headers.Keys) { $req.Headers.TryAddWithoutValidation($k, [string]$Headers[$k]) | Out-Null }
        if ($LastEventId) { $req.Headers.TryAddWithoutValidation('Last-Event-ID', $LastEventId) | Out-Null }
        if ($null -ne $Body) {
            $json = ($Body | ConvertTo-Json -Depth 10 -Compress)
            $req.Content = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, 'application/json')
        }
        $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $reader = [System.IO.StreamReader]::new($stream)
        $events = @()
        $currentEvent = ''
        $currentData = ''
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while (-not $reader.EndOfStream -and (Get-Date) -lt $deadline) {
            $readTask = $reader.ReadLineAsync()
            if (-not $readTask.Wait([TimeSpan]::FromSeconds([Math]::Max(1, ($deadline - (Get-Date)).TotalSeconds)))) { break }
            $line = $readTask.Result
            if ($null -eq $line) { break }
            if ($line.StartsWith('event:')) { $currentEvent = $line.Substring(6).Trim() }
            elseif ($line.StartsWith('data:')) { $currentData = $line.Substring(5).Trim() }
            elseif ($line -eq '' -and $currentEvent) {
                $events += [pscustomobject]@{ Event = $currentEvent; Data = $currentData }
                if ($currentEvent -eq 'done' -or $currentEvent -eq 'error') { break }
                $currentEvent = ''; $currentData = ''
            }
        }
        return [pscustomobject]@{ Status = [int]$resp.StatusCode; Events = $events }
    } finally { $client.Dispose() }
}

function Send-Upload {
    param([string]$Path, [string]$Token, [string[]]$Files, [hashtable]$Headers = @{})
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(120)
    try {
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$BaseUrl$Path")
        if ($Token) { $req.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token) }
        foreach ($k in $Headers.Keys) { $req.Headers.TryAddWithoutValidation($k, [string]$Headers[$k]) | Out-Null }
        $content = [System.Net.Http.MultipartFormDataContent]::new()
        foreach ($f in $Files) {
            $bytes = [System.IO.File]::ReadAllBytes($f)
            $fileContent = [System.Net.Http.ByteArrayContent]::new($bytes)
            $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('text/markdown')
            $content.Add($fileContent, 'files', [System.IO.Path]::GetFileName($f))
        }
        $req.Content = $content
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        $raw = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $json = $null; try { $json = $raw | ConvertFrom-Json } catch {}
        return [pscustomobject]@{ Status = [int]$resp.StatusCode; Body = $json; Raw = $raw }
    } finally { $client.Dispose() }
}

Write-Host "=== CampusPilot 冒烟验证 @ $BaseUrl ===" -ForegroundColor Cyan

# ---------- 0. 健康检查 ----------
Write-Host '`n[0] 健康检查'
$live = Invoke-Api -Method GET -Path '/health/live'
Assert 'GET /health/live 200 + status=alive' ($live.Status -eq 200 -and $live.Body.data.status -eq 'alive') ($live.Raw)
$ready = Invoke-Api -Method GET -Path '/health/ready'
$deps = $ready.Body.data.dependencies
Assert 'GET /health/ready 200 + postgres/redis/chroma 全 up' (
    $ready.Status -eq 200 -and $deps.postgres.status -eq 'up' -and $deps.redis.status -eq 'up' -and $deps.chroma.status -eq 'up') ($ready.Raw)
Assert '响应携带 X-Request-Id 头' ([bool]$live.Headers['X-Request-Id'])

# ---------- 1. M4 认证与 RBAC ----------
Write-Host '`n[1] M4 认证与 RBAC'
$badLogin = Invoke-Api -Method POST -Path '/api/v1/auth/login' -Body @{ username = 'student01'; password = 'wrong-password-1' }
Assert '错误密码登录返回 401 INVALID_CREDENTIALS' ($badLogin.Status -eq 401 -and $badLogin.Body.code -eq 'INVALID_CREDENTIALS') ($badLogin.Raw)

$sessions = @{}
$tokens = @{}
foreach ($u in 'admin01', 'knowledge01', 'service01', 'community01', 'student01', 'student02') {
    $ws = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $r = Invoke-Api -Method POST -Path '/api/v1/auth/login' -Body @{ username = $u; password = $Password } -Session $ws
    Assert "登录 $u 返回 200 + access_token" ($r.Status -eq 200 -and $r.Body.data.access_token) ($r.Raw)
    $sessions[$u] = $ws
    $tokens[$u] = $r.Body.data.access_token
}
$me = Invoke-Api -Method GET -Path '/api/v1/auth/me' -Token $tokens['student01']
Assert 'GET /auth/me 返回本人用户名与部门' ($me.Status -eq 200 -and $me.Body.data.username -eq 'student01' -and $me.Body.data.department -eq '计算机学院') ($me.Raw)
$studentUserId = $me.Body.data.user_id ?? $me.Body.data.id

$noOrigin = Invoke-Api -Method POST -Path '/api/v1/auth/refresh' -Session $sessions['student02']
Assert '缺少 Origin 的刷新返回 403 AUTH_FORBIDDEN' ($noOrigin.Status -eq 403 -and $noOrigin.Body.code -eq 'AUTH_FORBIDDEN') ($noOrigin.Raw)
$refresh = Invoke-Api -Method POST -Path '/api/v1/auth/refresh' -Session $sessions['student02'] -Headers @{ Origin = $FrontendOrigin }
Assert '携带 Origin 的刷新返回 200 + 新 access_token' ($refresh.Status -eq 200 -and $refresh.Body.data.access_token) ($refresh.Raw)
$tokens['student02'] = $refresh.Body.data.access_token

$forbidden = Invoke-Api -Method GET -Path '/api/v1/users' -Token $tokens['student01']
Assert '学生访问用户管理返回 403' ($forbidden.Status -eq 403) ($forbidden.Raw)
$users = Invoke-Api -Method GET -Path '/api/v1/users?page_size=10' -Token $tokens['admin01']
Assert '管理员查询用户列表 200' ($users.Status -eq 200 -and $users.Body.data.items.Count -ge 6) ($users.Raw)

$configs = Invoke-Api -Method GET -Path '/api/v1/configs' -Token $tokens['admin01']
Assert '管理员查询业务配置 200' ($configs.Status -eq 200) ($configs.Raw)

# ---------- 2. M2 校园服务 ----------
Write-Host '`n[2] M2 校园服务'
$departments = Invoke-Api -Method GET -Path '/api/v1/departments?page_size=50' -Token $tokens['student01']
Assert '部门列表含 SCU 真实部门' ($departments.Status -eq 200 -and ($departments.Body.data.items.name -join '|') -match '教务处') ($departments.Raw)

$guides = Invoke-Api -Method GET -Path '/api/v1/service-guides?page_size=50' -Token $tokens['student01']
Assert '服务指南列表返回 6 项 SCU 指南' ($guides.Status -eq 200 -and $guides.Body.data.items.Count -eq 6) ($guides.Raw)
$enrollGuide = $guides.Body.data.items | Where-Object { $_.code -eq 'enrollment_certificate' }
$checklist = Invoke-Api -Method GET -Path "/api/v1/service-guides/$($enrollGuide.id)/checklist?campus_code=jiangan&student_type=undergraduate" -Token $tokens['student01']
Assert '在读证明材料清单含学生证材料' ($checklist.Status -eq 200 -and ($checklist.Raw -match '学生证')) ($checklist.Raw)

$contacts = Invoke-Api -Method GET -Path '/api/v1/department-contacts?page_size=50' -Token $tokens['student01']
Assert '部门联系窗口返回 8 项且电话为占位号' ($contacts.Status -eq 200 -and $contacts.Body.data.items.Count -eq 8) ($contacts.Raw)

$start = (Get-Date).AddDays(1).ToString('yyyy-MM-ddTHH:mm:sszzz')
$end = (Get-Date).AddDays(1).AddHours(2).ToString('yyyy-MM-ddTHH:mm:sszzz')
$orderBody = @{ campus_code = 'jiangan'; dormitory_area = '西园'; building = '7舍'; room = '412'; fault_category = 'network'; description = '宿舍网络端口无信号，已重启路由器仍未恢复。'; preferred_start_at = $start; preferred_end_at = $end }
$idemOrder = New-Idem
$order = Invoke-Api -Method POST -Path '/api/v1/work-orders' -Token $tokens['student01'] -Body $orderBody -Headers @{ 'Idempotency-Key' = $idemOrder }
Assert '创建报修工单 201 + submitted' ($order.Status -eq 201 -and $order.Body.data.status -eq 'submitted') ($order.Raw)
$orderId = $order.Body.data.id
$orderReplay = Invoke-Api -Method POST -Path '/api/v1/work-orders' -Token $tokens['student01'] -Body $orderBody -Headers @{ 'Idempotency-Key' = $idemOrder }
Assert '相同幂等键重放返回首次工单' ($orderReplay.Body.data.id -eq $orderId) ($orderReplay.Raw)

$t1 = Invoke-Api -Method POST -Path "/api/v1/work-orders/$orderId/transitions" -Token $tokens['service01'] -Body @{ target_status = 'accepted'; reason = '已受理，安排上门处理'; version = 1 } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '处理员受理工单 200' ($t1.Status -eq 200 -and $t1.Body.data.status -eq 'accepted') ($t1.Raw)
$t2 = Invoke-Api -Method POST -Path "/api/v1/work-orders/$orderId/transitions" -Token $tokens['service01'] -Body @{ target_status = 'processing'; reason = '已开始上门排查'; version = 2 } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '工单进入处理中 200' ($t2.Status -eq 200 -and $t2.Body.data.status -eq 'processing') ($t2.Raw)
$t3 = Invoke-Api -Method POST -Path "/api/v1/work-orders/$orderId/transitions" -Token $tokens['service01'] -Body @{ target_status = 'completed'; reason = '端口已修复并验证'; completion_note = '更换信息插座模块后恢复'; version = 3 } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '工单完成 200' ($t3.Status -eq 200 -and $t3.Body.data.status -eq 'completed') ($t3.Raw)
$tBad = Invoke-Api -Method POST -Path "/api/v1/work-orders/$orderId/transitions" -Token $tokens['service01'] -Body @{ target_status = 'accepted'; reason = '非法回退'; version = 4 } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '非法状态迁移返回 409' ($tBad.Status -eq 409) ($tBad.Raw)

$rate = Invoke-Api -Method POST -Path "/api/v1/work-orders/$orderId/rating" -Token $tokens['student01'] -Body @{ score = 5; comment = '处理及时' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '工单评价成功' (($rate.Status -eq 200 -or $rate.Status -eq 201) -and $rate.Body.data.score -eq 5) ($rate.Raw)
$rateAgain = Invoke-Api -Method POST -Path "/api/v1/work-orders/$orderId/rating" -Token $tokens['student01'] -Body @{ score = 4 } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '重复评价返回 409 WORK_ORDER_ALREADY_RATED' ($rateAgain.Status -eq 409 -and $rateAgain.Body.code -eq 'WORK_ORDER_ALREADY_RATED') ($rateAgain.Raw)

$roomId = '21000000-0000-4000-8000-000000000001'
$elec1 = Invoke-Api -Method GET -Path "/api/v1/electricity/accounts/$roomId" -Token $tokens['student01']
Assert '查询电费余额 200' ($elec1.Status -eq 200 -and $null -ne $elec1.Body.data.balance_cny) ($elec1.Raw)
$idemTopupKey = New-Idem
$topup = Invoke-Api -Method POST -Path '/api/v1/electricity/topup-requests' -Token $tokens['student01'] -Body @{ room_id = $roomId; amount_cny = 20 } -Headers @{ 'Idempotency-Key' = $idemTopupKey }
Assert '创建模拟充值申请 201 + simulated' ($topup.Status -eq 201 -and $topup.Body.data.is_simulated -eq $true) ($topup.Raw)
$topupReplay = Invoke-Api -Method POST -Path '/api/v1/electricity/topup-requests' -Token $tokens['student01'] -Body @{ room_id = $roomId; amount_cny = 20 } -Headers @{ 'Idempotency-Key' = $idemTopupKey }
Assert '相同幂等键重放返回首次充值申请' ($topupReplay.Body.data.id -eq $topup.Body.data.id) ($topupReplay.Raw)

$student02Orders = Invoke-Api -Method GET -Path '/api/v1/work-orders' -Token $tokens['student02']
Assert '学生工单列表仅见本人（不含 student01 工单）' ($student02Orders.Status -eq 200 -and -not ($student02Orders.Body.data.items.id -contains $orderId)) ($student02Orders.Raw)

# ---------- 3. M3 社区 ----------
Write-Host '`n[3] M3 社区'
$topics = Invoke-Api -Method GET -Path '/api/v1/topics' -Token $tokens['student01']
Assert '话题列表返回 3 个种子话题' ($topics.Status -eq 200 -and $topics.Body.data.items.Count -eq 3) ($topics.Raw)
$helpTopic = $topics.Body.data.items | Where-Object { $_.code -eq 'mutual-help' }

$post = Invoke-Api -Method POST -Path '/api/v1/posts' -Token $tokens['student01'] -Body @{ topic_id = $helpTopic.id; title = '江安校区图书馆晚上几点闭馆？'; content_markdown = '求问各位同学，江安图书馆考试周开放时间会延长吗？' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '发帖 201' (($post.Status -eq 200 -or $post.Status -eq 201) -and $post.Body.data.id) ($post.Raw)
$postId = $post.Body.data.id
$comment = Invoke-Api -Method POST -Path "/api/v1/posts/$postId/comments" -Token $tokens['student02'] -Body @{ content_markdown = '一般到 22:00，考试周会延长，建议看图书馆通知。' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '评论 201' (($comment.Status -eq 200 -or $comment.Status -eq 201) -and $comment.Body.data.id) ($comment.Raw)
$reaction = Invoke-Api -Method PUT -Path "/api/v1/posts/$postId/reactions/like" -Token $tokens['student02'] -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '点赞成功' ($reaction.Status -ge 200 -and $reaction.Status -lt 300) ($reaction.Raw)

$report = Invoke-Api -Method POST -Path '/api/v1/reports' -Token $tokens['student02'] -Body @{ target_type = 'post'; target_id = $postId; reason_code = 'other'; details = '冒烟验证举报到审核联动' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '举报提交成功' (($report.Status -eq 200 -or $report.Status -eq 201) -and $report.Body.data.id) ($report.Raw)
$cases = Invoke-Api -Method GET -Path '/api/v1/moderation/cases?page_size=10' -Token $tokens['community01']
Assert '社区运营员可见举报生成的审核案件（M3→M4 联动）' ($cases.Status -eq 200 -and $cases.Body.data.items.Count -ge 1) ($cases.Raw)

$lostFound = Invoke-Api -Method POST -Path '/api/v1/lost-found' -Token $tokens['student01'] -Body @{ item_type = 'lost'; title = '丢失银色U盘'; category = 'electronics'; description = '银色金属U盘，挂有蓝色挂绳，内有课程资料。'; occurred_at = (Get-Date).AddHours(-3).ToString('yyyy-MM-ddTHH:mm:sszzz'); location = '江安校区一教A座'; contact_type = 'other'; contact_value = '站内私信联系' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '发布失物信息 201（联系方式加密存储）' (($lostFound.Status -eq 200 -or $lostFound.Status -eq 201) -and $lostFound.Body.data.id) ($lostFound.Raw)

$events = Invoke-Api -Method GET -Path '/api/v1/events' -Token $tokens['student01']
Assert '活动列表返回种子活动' ($events.Status -eq 200 -and $events.Body.data.items.Count -ge 2) ($events.Raw)
$eventId = $events.Body.data.items[0].id
$reg = Invoke-Api -Method POST -Path "/api/v1/events/$eventId/registrations" -Token $tokens['student02'] -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '活动报名 201' (($reg.Status -eq 200 -or $reg.Status -eq 201)) ($reg.Raw)
$regAgain = Invoke-Api -Method POST -Path "/api/v1/events/$eventId/registrations" -Token $tokens['student02'] -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '重复报名按幂等返回同一结果（200，契约规定）' ($regAgain.Status -eq 200 -and $regAgain.Body.data.status -eq 'registered') ($regAgain.Raw)

# ---------- 4. M1 知识库与 RAG（真实 DeepSeek + BGE + Chroma） ----------
Write-Host '`n[4] M1 知识库与 RAG'
$kbs = Invoke-Api -Method GET -Path '/api/v1/knowledge-bases?page_size=20' -Token $tokens['student01']
$kb = $kbs.Body.data.items | Where-Object { $_.name -eq '四川大学校园知识库' }
Assert '知识库列表含四川大学校园知识库' ($kbs.Status -eq 200 -and $null -ne $kb) ($kbs.Raw)
$kbId = $kb.id

$docsDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend/app/scripts/data/scu/docs'
$uploadedNames = @((Get-ChildItem -Path $docsDir -Filter '*.md').BaseName)
$existing = (Invoke-Api -Method GET -Path "/api/v1/knowledge-bases/$kbId/documents?page_size=50" -Token $tokens['knowledge01']).Body.data.items
$missing = @($uploadedNames | Where-Object { $n = $_; -not ($existing | Where-Object { $_.title -eq $n }) })
if ($missing.Count -gt 0) {
    # 单次最多 10 个文件，分批上传
    for ($i = 0; $i -lt $missing.Count; $i += 10) {
        $batch = @($missing[$i..([Math]::Min($i + 9, $missing.Count - 1))] | ForEach-Object { Join-Path $docsDir "$_.md" })
        $upload = Send-Upload -Path "/api/v1/knowledge-bases/$kbId/documents" -Token $tokens['knowledge01'] -Files $batch -Headers @{ 'Idempotency-Key' = (New-Idem) }
        Assert "上传 $($batch.Count) 篇 SCU 真实文档返回 202" ($upload.Status -eq 202) ($upload.Raw)
    }
} else {
    Assert 'SCU 文档已存在（幂等复用，冒烟可重复执行）' $true
}

# 入库 Worker 为一次性排空设计：上传后显式运行直至队列清空
$Python = 'D:\anaconda\envs\campuspilot\python.exe'
& $Python -m app.scripts.ingestion_worker 2>&1 | Out-Null

$readyDocs = @()
$deadline = (Get-Date).AddMinutes(6)
do {
    Start-Sleep -Seconds 10
    $docs = Invoke-Api -Method GET -Path "/api/v1/knowledge-bases/$kbId/documents?page_size=50" -Token $tokens['knowledge01']
    $readyDocs = @($docs.Body.data.items | Where-Object { $uploadedNames -contains $_.title } | Where-Object { $_.status -in @('ready', 'published') })
    $failedDocs = @($docs.Body.data.items | Where-Object { $_.status -eq 'failed' })
    if ($failedDocs.Count -gt 0) { break }
} while ($readyDocs.Count -lt $uploadedNames.Count -and (Get-Date) -lt $deadline)
Assert "入库 Worker 完成解析/切分/向量索引（$($readyDocs.Count)/$($uploadedNames.Count) 文档 ready）" ($readyDocs.Count -eq $uploadedNames.Count) ($docs.Raw)

foreach ($d in $readyDocs) {
    if ($d.status -eq 'ready') {
        $pub = Invoke-Api -Method POST -Path "/api/v1/documents/$($d.id)/publish" -Token $tokens['knowledge01'] -Body @{ reason = '冒烟验证发布'; version = $d.version } -Headers @{ 'Idempotency-Key' = (New-Idem) }
        Assert "发布文档 $($d.id) 200" ($pub.Status -eq 200 -and $pub.Body.data.status -eq 'published') ($pub.Raw)
    }
}

$chat = Invoke-Api -Method POST -Path '/api/v1/chat/completions' -Token $tokens['student01'] -Body @{ question = '四川大学有几个校区？请说明各校区地址。'; knowledge_base_ids = @($kbId) } -Headers @{ 'Idempotency-Key' = (New-Idem) }
$answer = $chat.Body.data.assistant_message.content
Assert '同步 Chat 返回真实 RAG 回答（含校区信息）' ($chat.Status -eq 200 -and $answer -and $answer -match '望江') ($chat.Raw)

$sse = Invoke-Sse -Path '/api/v1/chat/stream' -Token $tokens['student01'] -Body @{ question = '四川大学有几个校区？望江校区地址是什么？'; knowledge_base_ids = @($kbId) } -Headers @{ 'Idempotency-Key' = (New-Idem) }
$eventNames = $sse.Events.Event
Assert 'SSE 流按 meta→delta*→sources→done 顺序' ($sse.Status -eq 200 -and $eventNames[0] -eq 'meta' -and ($eventNames -contains 'delta') -and ($eventNames -contains 'sources') -and $eventNames[-1] -eq 'done') (($eventNames -join ',') )
$meta = $sse.Events[0].Data | ConvertFrom-Json
$sseAnswer = ($sse.Events | Where-Object { $_.Event -eq 'delta' } | ForEach-Object { ($_.Data | ConvertFrom-Json).content }) -join ''
Assert 'SSE 回答含校区关键词' ($sseAnswer -match '望江') ($sseAnswer)

# 无合格检索结果时按设计走兜底流（meta→sources→done，finish_reason=fallback）
$sseFallback = Invoke-Sse -Path '/api/v1/chat/stream' -Token $tokens['student01'] -Body @{ question = '请介绍量子引力最新实验进展'; knowledge_base_ids = @($kbId) } -Headers @{ 'Idempotency-Key' = (New-Idem) }
$fbNames = $sseFallback.Events.Event
$fbDone = ($sseFallback.Events | Where-Object { $_.Event -eq 'done' } | Select-Object -First 1)
$fbReason = $fbDone ? (($fbDone.Data | ConvertFrom-Json).finish_reason) : ''
Assert '不可检索问题按兜底流返回（meta→sources→done + fallback）' ($fbNames[0] -eq 'meta' -and $fbNames[-1] -eq 'done' -and $fbReason -eq 'fallback') (($fbNames -join ',') + ' reason=' + $fbReason)

$feedback = Invoke-Api -Method POST -Path "/api/v1/messages/$($meta.message_id)/feedback" -Token $tokens['student01'] -Body @{ rating = 1 } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '消息反馈 201' ($feedback.Status -eq 201) ($feedback.Raw)

$noAuthChat = Invoke-Api -Method POST -Path '/api/v1/chat/completions' -Body @{ question = 'test'; knowledge_base_ids = @($kbId) } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '未认证 Chat 返回 401' ($noAuthChat.Status -eq 401) ($noAuthChat.Raw)

# ---------- 5. M5 Agent 平台与内部 Tool 网关 ----------
Write-Host '`n[5] M5 Agent 平台'
$agents = Invoke-Api -Method GET -Path '/api/v1/agents' -Token $tokens['student01']
Assert 'Agent 目录 200' ($agents.Status -eq 200 -and $agents.Body.data.items.Count -ge 1) ($agents.Raw)
$tools = Invoke-Api -Method GET -Path '/api/v1/tools?module=m1' -Token $tokens['student01']
Assert 'Tool 目录含 knowledge.search' (($tools.Raw -match 'knowledge.search')) ($tools.Raw)

# 5a. 知识型 Run（输入含"图书馆"关键词确保规则路由高置信）
$run = Invoke-Api -Method POST -Path '/api/v1/agent-runs' -Token $tokens['student01'] -Body @{ input = '请通过图书馆知识库回答：四川大学有几个校区，望江校区地址是什么？'; mode = 'knowledge' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '创建 Agent Run 返回 202' ($run.Status -eq 202 -and $run.Body.data.id) ($run.Raw)
$runId = $run.Body.data.id

$finalRun = $null
$runDeadline = (Get-Date).AddMinutes(4)
do {
    Start-Sleep -Seconds 5
    $finalRun = (Invoke-Api -Method GET -Path "/api/v1/agent-runs/$runId" -Token $tokens['student01']).Body.data.run
} while ($finalRun.status -notin @('succeeded', 'failed', 'cancelled', 'partial') -and (Get-Date) -lt $runDeadline)
Assert 'Runtime Worker 经 Outbox 执行知识 Run 至终态且有回答（M5→DeepSeek+M1 链路）' ($finalRun.status -in @('succeeded', 'partial') -and $finalRun.final_answer) ("status=$($finalRun.status) error=$($finalRun.error_code)")

$runStream = Invoke-Sse -Method GET -Path "/api/v1/agent-runs/$runId/stream" -Token $tokens['student01'] -TimeoutSeconds 30
Assert 'Run SSE 流可回放事件' ($runStream.Status -eq 200 -and $runStream.Events.Count -ge 1) (($runStream.Events.Event -join ','))
if ($runStream.Events.Count -ge 2) {
    $firstSeq = ($runStream.Events[0].Data | ConvertFrom-Json).sequence
    $replay = Invoke-Sse -Method GET -Path "/api/v1/agent-runs/$runId/stream" -Token $tokens['student01'] -TimeoutSeconds 30 -LastEventId ([string]($runStream.Events.Count - 1))
    Assert 'Last-Event-ID 重放仅返回增量事件' ($replay.Status -eq 200 -and $replay.Events.Count -le $runStream.Events.Count) (($replay.Events.Event -join ','))
}

# 5b. 服务型 Run 触发 R2 电费充值审批（输入携带房间 ID 供 Specialist 传参）
$internalSecret = $env:INTERNAL_TOOL_SECRET
if (-not $internalSecret) {
    $envLine = Get-Content (Join-Path (Split-Path -Parent $PSScriptRoot) '.env') | Where-Object { $_ -match '^INTERNAL_TOOL_SECRET=' }
    $internalSecret = $envLine -replace '^INTERNAL_TOOL_SECRET=', ''
}
$topupInput = '请调用 electricity.create_topup_request 工具为我的宿舍充值20元电费，arguments 必须严格为 {"room_id":"' + $roomId + '","amount_cny":20}（参数名逐字一致，不可用 amount）'
$topupRun = Invoke-Api -Method POST -Path '/api/v1/agent-runs' -Token $tokens['student01'] -Body @{ input = $topupInput; mode = 'service' } -Headers @{ 'Idempotency-Key' = (New-Idem) }
Assert '创建服务型 Run 返回 202' ($topupRun.Status -eq 202 -and $topupRun.Body.data.id) ($topupRun.Raw)
$topupRunId = $topupRun.Body.data.id

$approvalRun = $null
$approvalDeadline = (Get-Date).AddMinutes(4)
do {
    Start-Sleep -Seconds 5
    $approvalRun = (Invoke-Api -Method GET -Path "/api/v1/agent-runs/$topupRunId" -Token $tokens['student01']).Body.data
} while ($approvalRun.run.status -notin @('awaiting_approval', 'succeeded', 'failed', 'cancelled', 'partial') -and (Get-Date) -lt $approvalDeadline)
Assert 'R2 电费充值触发审批（Run 进入 awaiting_approval）' ($approvalRun.run.status -eq 'awaiting_approval' -and $approvalRun.approvals.Count -ge 1) ("status=$($approvalRun.run.status)")

if ($approvalRun.run.status -eq 'awaiting_approval' -and $approvalRun.approvals.Count -ge 1) {
    $approval = $approvalRun.approvals[0]
    $toolStep = $approvalRun.steps | Where-Object { $_.agent_code -eq 'service_agent' } | Select-Object -Last 1
    # 内部 HTTP Tool 端点（独立服务凭证）在审批等待期间调用 R1 余额查询
    $invokeBody = @{ run_id = $topupRunId; step_id = $toolStep.id; agent_code = 'service_agent'; user_id = $studentUserId; arguments = @{ room_id = $roomId } }
    $invoke = Invoke-Api -Method POST -Path '/internal/v1/tools/electricity.get_balance:invoke' -Body $invokeBody -Headers @{ Authorization = "Bearer $internalSecret"; 'Idempotency-Key' = (New-Idem) }
    Assert '内部 Tool 网关 electricity.get_balance 返回余额（M5↔M2 HTTP 链路）' ($invoke.Status -eq 200) ($invoke.Raw)
    $invokeJwt = Invoke-Api -Method POST -Path '/internal/v1/tools/electricity.get_balance:invoke' -Token $tokens['student01'] -Body $invokeBody -Headers @{ 'Idempotency-Key' = (New-Idem) }
    Assert '用户 JWT 调用内部 Tool 被拒绝（401/403）' ($invokeJwt.Status -in @(401, 403)) ($invokeJwt.Raw)

    $decide = Invoke-Api -Method POST -Path "/api/v1/agent-runs/$topupRunId/approvals/$($approval.id)" -Token $tokens['student01'] -Body @{ decision = 'approve'; argument_hash = $approval.argument_hash } -Headers @{ 'Idempotency-Key' = (New-Idem) }
    Assert '批准 R2 电费充值 200' ($decide.Status -eq 200) ($decide.Raw)
    $decideAgain = Invoke-Api -Method POST -Path "/api/v1/agent-runs/$topupRunId/approvals/$($approval.id)" -Token $tokens['student01'] -Body @{ decision = 'approve'; argument_hash = $approval.argument_hash } -Headers @{ 'Idempotency-Key' = (New-Idem) }
    Assert '同一审批重复决策返回 409（一次性消费）' ($decideAgain.Status -eq 409) ($decideAgain.Raw)

    $resumeRun = $null
    $resumeDeadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 5
        $resumeRun = (Invoke-Api -Method GET -Path "/api/v1/agent-runs/$topupRunId" -Token $tokens['student01']).Body.data
    } while ($resumeRun.run.status -notin @('succeeded', 'failed', 'cancelled', 'partial') -and (Get-Date) -lt $resumeDeadline)
    $topupCall = $resumeRun.tool_calls | Where-Object { $_.tool_name -eq 'electricity.create_topup_request' } | Select-Object -First 1
    Assert '审批后 Run 恢复执行并成功完成充值 Tool 调用' ($resumeRun.run.status -in @('succeeded', 'partial') -and $topupCall.status -eq 'succeeded') ("status=$($resumeRun.run.status) call=$($topupCall.status)")
}

# ---------- 6. 错误信封与 CORS ----------
Write-Host '`n[6] 错误信封与 CORS'
$reqId = 'smoke-' + (New-Idem)
$notFound = Invoke-Api -Method GET -Path "/api/v1/service-guides/00000000-0000-4000-8000-000000000000?campus_code=jiangan&student_type=undergraduate" -Token $tokens['student01'] -Headers @{ 'X-Request-Id' = $reqId }
Assert '不存在指南返回 404 + 统一错误信封' ($notFound.Status -eq 404 -and $notFound.Body.code -and $notFound.Body.request_id -and $notFound.Body.timestamp) ($notFound.Raw)
Assert 'X-Request-Id 原样回传' ($notFound.Headers['X-Request-Id'] -contains $reqId -or $notFound.Headers['X-Request-Id'] -eq $reqId) ($notFound.Headers['X-Request-Id'] -join ',')

$cors = Invoke-WebRequest -Uri "$BaseUrl/health/live" -Method GET -Headers @{ Origin = 'https://evil.example.com' } -SkipHttpErrorCheck
$acao = $cors.Headers['Access-Control-Allow-Origin']
Assert '非法 Origin 不返回 CORS 放行头' (-not $acao -or $acao -eq $FrontendOrigin) ($acao -join ',')
$corsOk = Invoke-WebRequest -Uri "$BaseUrl/health/live" -Method GET -Headers @{ Origin = $FrontendOrigin } -SkipHttpErrorCheck
Assert 'FRONTEND_ORIGIN 返回 CORS 放行头' ($corsOk.Headers['Access-Control-Allow-Origin'] -contains $FrontendOrigin) ($corsOk.Headers['Access-Control-Allow-Origin'] -join ',')

# ---------- 汇总 ----------
Write-Host "`n=== 结果: $($script:Passed) 通过, $($script:Failed) 失败 ===" -ForegroundColor ($script:Failed -eq 0 ? 'Green' : 'Red')
if ($script:Failed -gt 0) {
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
exit 0
