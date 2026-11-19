import asyncio

import httpx


async def download_pdf(url, *, client, timeout_ms=30000, max_bytes=20 * 1024 * 1024):
    try:
        async with client.stream('GET', url, follow_redirects=True, timeout=timeout_ms / 1000, headers={'accept': 'application/pdf,application/octet-stream;q=0.9,*/*;q=0.1'}) as response:
            if not response.is_success:
                return {'ok': False, 'reason': 'http', 'message': f'HTTP {response.status_code}'}
            content_type = response.headers.get('content-type', '')
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    return {'ok': False, 'reason': 'too_large', 'message': f'文件超过大小上限 {max_bytes} 字节'}
                chunks.append(chunk)
            body = b''.join(chunks)
            if not body:
                return {'ok': False, 'reason': 'empty', 'message': '响应内容为空'}
            if not body.startswith(b'%PDF-'):
                message = '响应缺少 PDF 魔数' if 'pdf' in content_type.lower() else f"响应不是 PDF：{content_type or '未知类型'}"
                return {'ok': False, 'reason': 'format', 'message': message}
            return {'ok': True, 'body': body, 'contentType': content_type or 'application/pdf', 'finalUrl': str(response.url) or url}
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException:
        return {'ok': False, 'reason': 'timeout', 'message': '下载超时'}
    except httpx.RequestError as error:
        return {'ok': False, 'reason': 'network', 'message': str(error)}
