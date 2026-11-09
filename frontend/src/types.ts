export type Summary = {
  today_sales: number
  today_bills: number
  total_products: number
  low_stock: number
  inventory_value: number
  sales_trend: { date: string; full_date: string; sales: number }[]
}

export type Product = {
  id: number
  sku: string
  name: string
  category: string
  unit: string
  price: number
  cost_price: number
  stock_qty: number
  reorder_level: number
}

export type Sale = {
  id: number
  bill_number: string
  customer_name: string | null
  payment_method: string
  status: string
  total: number
  created_at: string
}
