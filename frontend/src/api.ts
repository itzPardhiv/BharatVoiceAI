import type { Product, Sale, Summary } from './types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
const STORE_API_KEY = import.meta.env.VITE_STORE_API_KEY?.trim()

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  // Optional local/demo store credential. Never put a Gnani key in VITE_* variables.
  if (STORE_API_KEY) headers.set('X-Store-API-Key', STORE_API_KEY)

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  } catch {
    throw new Error(`Cannot reach BharatVoice backend at ${API_BASE}. Check that the backend is running.`)
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const payload = await response.json() as { detail?: string | { msg?: string }[] }
      if (typeof payload.detail === 'string') detail = payload.detail
      else if (Array.isArray(payload.detail)) detail = payload.detail.map(item => item.msg ?? 'Invalid input').join('; ')
    } catch { /* Keep the HTTP status message when the response is not JSON. */ }
    throw new Error(detail)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string; service?: string }>('/api/health'),
  summary: () => request<Summary>('/api/dashboard/summary'),
  products: (search = '', lowStock = false) => {
    const params = new URLSearchParams()
    if (search.trim()) params.set('search', search.trim())
    if (lowStock) params.set('low_stock_only', 'true')
    const query = params.toString()
    return request<Product[]>(`/api/products${query ? `?${query}` : ''}`)
  },
  createProduct: (data: { sku: string; name: string; category: string; unit: string; price: number; cost_price: number; stock_qty: number; reorder_level?: number }) =>
    request<Product>('/api/products', { method: 'POST', body: JSON.stringify(data) }),
  updateProduct: (id: number, data: Partial<Product>) =>
    request<Product>(`/api/products/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteProduct: (id: number) =>
    request<Product>(`/api/products/${id}`, { method: 'DELETE' }),
  adjustStock: (id: number, quantity_change: number, reason: string, notes?: string) =>
    request<Product>(`/api/products/${id}/stock-adjust`, { method: 'POST', body: JSON.stringify({ quantity_change, reason, notes }) }),
  recentSales: (limit = 8) => request<Sale[]>(`/api/sales/recent?limit=${limit}`),
  createSale: (data: { bill_number?: string; customer_name?: string; customer_id?: number; payment_method: string; items: { sku: string; quantity: number; unit_price?: number }[]; discount?: number }) =>
    request<any>('/api/sales', { method: 'POST', body: JSON.stringify(data) }),
  refundSale: (id: number, reason?: string) =>
    request<any>(`/api/sales/${id}/refund`, { method: 'POST', body: JSON.stringify({ reason: reason || 'Customer refund' }) }),
  voiceProcess: (query: string, confirmed = false, actionPayload: any = null, languageCode = 'en-IN') =>
    request<{
      intent: string
      reply: string
      action_required: boolean
      action_type?: string
      action_payload?: any
      data?: any
      audio_base64?: string
      tts_format?: string
    }>('/api/voice/process', {
      method: 'POST',
      body: JSON.stringify({ query, confirmed, action_payload: actionPayload, language_code: languageCode })
    }),
  voiceTranscribe: async (audioBlob: Blob, languageCode = 'en-IN') => {
    const formData = new FormData()
    formData.append('audio', audioBlob, 'recording.wav')
    formData.append('language_code', languageCode)
    const res = await fetch(`${API_BASE}/api/voice/transcribe`, {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) throw new Error('Failed to transcribe audio via Gnani Prisma STT')
    return res.json() as Promise<{
      transcript?: string
      transcription?: string
      success?: boolean
      provider?: string
      message?: string
    }>
  },
  voiceTTS: (text: string, languageCode = 'en-IN', voiceGender = 'female') =>
    request<{
      success: boolean
      audio_base64?: string
      format?: string
      provider?: string
      message?: string
    }>('/api/voice/tts', {
      method: 'POST',
      body: JSON.stringify({ text, language_code: languageCode, voice_gender: voiceGender }),
    }),
  suppliers: () => request<any[]>('/api/suppliers'),
  createSupplier: (data: { name: string; contact_person?: string; phone?: string; address?: string; gstin?: string }) =>
    request<any>('/api/suppliers', { method: 'POST', body: JSON.stringify(data) }),
  purchases: () => request<any[]>('/api/purchases'),
  createPurchase: (data: { supplier_id: number; invoice_number: string; paid_amount?: number; payment_method?: string; items: { sku: string; quantity: number; unit_cost: number }[]; notes?: string }) =>
    request<any>('/api/purchases', { method: 'POST', body: JSON.stringify(data) }),
  customers: () => request<any[]>('/api/customers'),
  createCustomer: (data: { name: string; phone?: string; address?: string }) =>
    request<any>('/api/customers', { method: 'POST', body: JSON.stringify(data) }),
  customerPayment: (id: number, amount: number, paymentMethod = 'CASH', notes?: string) =>
    request<any>(`/api/customers/${id}/payment`, { method: 'POST', body: JSON.stringify({ amount, payment_method: paymentMethod, notes }) }),
  expenses: () => request<any[]>('/api/expenses'),
  createExpense: (data: { category: string; amount: number; description: string; payment_method?: string }) =>
    request<any>('/api/expenses', { method: 'POST', body: JSON.stringify(data) }),
  reverseExpense: (id: number, reason = 'Expense reversal') =>
    request<any>(`/api/expenses/${id}/reverse`, { method: 'POST', body: JSON.stringify({ reason }) }),
  cashClosing: (dateStr?: string) => request<any>(`/api/accounts/cash-closing${dateStr ? `?date_str=${dateStr}` : ''}`),
  financeMetrics: (period = 'today') => request<any>(`/api/finance/metrics?period=${period}`),
  financeValuation: () => request<any>('/api/finance/valuation'),
  getStoreProfile: () => request<{
    id: number
    store_id: string
    store_name: string
    owner_name?: string
    phone?: string
    email?: string
    address?: string
    gstin?: string
    currency_symbol: string
    currency_code: string
    default_tax_rate: number
    invoice_footer?: string
    accessibility_preferences_json?: string
    updated_at: string
  }>('/api/settings/profile'),
  updateStoreProfile: (data: {
    store_name?: string
    owner_name?: string
    phone?: string
    email?: string
    address?: string
    gstin?: string
    currency_symbol?: string
    currency_code?: string
    default_tax_rate?: number
    invoice_footer?: string
    accessibility_preferences_json?: string
  }) => request<any>('/api/settings/profile', {
    method: 'PUT',
    body: JSON.stringify(data),
  }),
}
