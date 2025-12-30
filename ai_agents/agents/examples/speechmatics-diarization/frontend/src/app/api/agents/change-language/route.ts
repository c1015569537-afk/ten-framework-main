import { NextRequest, NextResponse } from 'next/server'
import axios from 'axios'

export async function POST(request: NextRequest) {
  try {
    const { AGENT_SERVER_URL } = process.env as { AGENT_SERVER_URL?: string }
    if (!AGENT_SERVER_URL) {
      throw new Error('AGENT_SERVER_URL not set')
    }

    const body = await request.json()
    const { request_id, channel_name, language } = body

    const payload = {
      request_id,
      channel_name,
      properties: {
        // Speechmatics 语言配置（使用节点名称 "stt" 而不是 addon 名称）
        stt: {
          params: {
            language: language || "en",
          },
        },
      },
    }

    // 调用后端的 configure 端点
    const resp = await axios.post(`${AGENT_SERVER_URL}/configure`, payload)
    return NextResponse.json(resp.data, { status: resp.status })
  } catch (error: any) {
    console.error('Error changing language:', error?.message || error)
    return NextResponse.json({ code: '1', data: null, msg: 'Internal Server Error' }, { status: 500 })
  }
}
