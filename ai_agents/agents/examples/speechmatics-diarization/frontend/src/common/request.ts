import axios from 'axios'

//const genUUID = () => crypto.randomUUID()

const genUUID = () => {
  // Use crypto.randomUUID() if it's available (in secure contexts)
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for insecure contexts or older browsers
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export const apiGenAgoraData = async (config: { userId: number, channel: string }) => {
  const url = '/api/token/generate'
  const data = { request_id: genUUID(), uid: config.userId, channel_name: config.channel }
  const resp = await axios.post(url, data)
  const raw = resp.data || {}
  const code = raw.code ?? raw.status ?? (raw.success === true ? 0 : 1)
  const ok = code === 0 || code === '0' || code === 'success' || raw.success === true
  const msg = raw.msg ?? raw.message ?? raw.status ?? (ok ? 'ok' : 'error')
  return { ok, code, msg, data: raw.data }
}

export const apiStartService = async (config: { channel: string, userId: number, graphName?: string, language?: string }) => {
  const url = '/api/agents/start'
  const data = {
    request_id: genUUID(),
    channel_name: config.channel,
    user_uid: config.userId,
    graph_name: config.graphName || 'diarization_demo',
    language: config.language || 'en',  // 语言代码，默认英文，由 API 路由代理处理
  }
  const resp = await axios.post(url, data)
  const raw = resp.data || {}
  const code = raw.code ?? raw.status ?? (raw.success === true ? 0 : 1)
  const ok = code === 0 || code === '0' || code === 'success' || raw.success === true
  const msg = raw.msg ?? raw.message ?? raw.status ?? (ok ? 'ok' : 'error')
  return { ok, code, msg, data: raw.data }
}

export const apiStopService = async (channel: string) => {
  const url = '/api/agents/stop'
  const data = {
    request_id: crypto.randomUUID(),
    channel_name: channel,
  }
  const resp = await axios.post(url, data)
  return resp.data
}

export const apiPing = async (channel: string) => {
  const url = '/api/agents/ping'
  const data = {
    request_id: crypto.randomUUID(),
    channel_name: channel,
  }
  const resp = await axios.post(url, data)
  return resp.data
}

export const apiChangeLanguage = async (config: { channel: string, language: string }) => {
  const url = '/api/agents/change-language'
  const data = {
    request_id: genUUID(),
    channel_name: config.channel,
    language: config.language,
  }
  const resp = await axios.post(url, data)
  const raw = resp.data || {}
  const code = raw.code ?? raw.status ?? (raw.success === true ? 0 : 1)
  const ok = code === 0 || code === '0' || code === 'success' || raw.success === true
  const msg = raw.msg ?? raw.message ?? raw.status ?? (ok ? 'ok' : 'error')
  return { ok, code, msg, data: raw.data }
}
