"""One DashScope chat request per stage. Only safe failure details reach the job."""
import json
import time

import httpx


def call_model(config, stage, model, system, content, limit, *, json_mode=False):
    key = config.get('ALIBABA_API_KEY')
    base = config.get('ALIBABA_BASE_URL', '').rstrip('/')
    if not key or not base or not model:
        raise ValueError(f'请先配置阿里云百炼的{stage}模型、地址和密钥。')
    route = config.get('ALIBABA_NETWORK_MODE', 'system')
    if route not in ('direct', 'system'):
        raise ValueError('百炼网络设置只支持 direct 或 system。')
    payload = {
        'model': model,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
        'enable_thinking': False, 'max_tokens': limit, 'temperature': .1,
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    label = f'阿里云百炼{stage}'
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False,
                          trust_env=(route == 'system')) as client:
            response = client.post(base + '/chat/completions',
                                   headers={'Authorization': 'Bearer ' + key}, json=payload)
    except httpx.ProxyError:
        raise ValueError(f'{label}连接代理失败。请检查工作台的代理设置后重试。') from None
    except httpx.ConnectTimeout:
        raise ValueError(f'{label}建立连接超时。请检查本机到百炼的网络后重试。') from None
    except httpx.ConnectError:
        raise ValueError(f'{label}未能建立连接。请检查网络、域名解析和证书后重试。') from None
    except httpx.TimeoutException:
        raise ValueError(f'{label}请求超时，尚未收到完整结果。已停止本次请求，可手动重试。') from None
    except (httpx.ReadError, httpx.RemoteProtocolError):
        raise ValueError(f'{label}接收响应时连接断开。已停止本次请求，可手动重试。') from None
    except httpx.HTTPError:
        raise ValueError(f'{label}网络传输中断。已停止本次请求，可手动重试。') from None
    if not response.is_success:
        reason = {
            400: '请检查模型与输入格式',
            401: '此接入地址的密钥无效，请核对百炼密钥及其所属地域',
            403: '请检查工作空间的模型权限或服务开通状态',
            404: '请检查模型名称、地域和接入地址',
            429: '请稍后重试并检查调用限额',
        }.get(response.status_code, '请稍后重试')
        raise ValueError(f'{label}返回 HTTP {response.status_code}。{reason}。')
    try:
        data = response.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ValueError(f'{label}输出达到长度上限，方案未生成完整。')
        answer = choice['message']['content'].strip()
        if answer.startswith('```'):
            answer = answer.split('\n', 1)[1].rsplit('```', 1)[0]
        result = json.loads(answer)
        usage = data.get('usage') or {}
        return result, {'model': model, 'elapsed_s': round(time.monotonic() - started, 2),
                        'input_tokens': usage.get('prompt_tokens', 0),
                        'output_tokens': usage.get('completion_tokens', 0)}
    except (AttributeError, KeyError, IndexError, TypeError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f'{label}返回的方案格式不完整，当前设置已保留。') from None
