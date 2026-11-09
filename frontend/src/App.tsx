import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, ArrowDownRight, ArrowUpRight, AudioLines, Bell, Boxes, CalendarDays,
  CheckCircle2, ChevronDown, CircleHelp, Command, Download, LayoutDashboard,
  Menu, Mic, Package, Plus, Search, Settings2, ShoppingBag, ShoppingCart,
  Sparkles, Truck, Users, Wallet, X, AlertTriangle, ReceiptText, ChartNoAxesCombined,
  Printer, RefreshCw, RotateCcw, Send, Volume2, Trash2
} from 'lucide-react'
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis
} from 'recharts'
import { api } from './api'
import type { Product, Sale, Summary } from './types'
import Onboarding, { type StoreProfile } from './Onboarding'
import { LANGUAGES, LanguageProvider, LanguageSelect, useLanguage } from './i18n'

type Section = 'Overview' | 'Products & Stock' | 'Bills & Sales' | 'Purchasing' | 'Customers & Staff' | 'Reports'

const money = (value: number) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0
}).format(value)

const navItems: { label: Section; icon: typeof LayoutDashboard }[] = [
  { label: 'Overview', icon: LayoutDashboard },
  { label: 'Products & Stock', icon: Boxes },
  { label: 'Bills & Sales', icon: ReceiptText },
  { label: 'Purchasing', icon: Truck },
  { label: 'Customers & Staff', icon: Users },
  { label: 'Reports', icon: ChartNoAxesCombined },
]

function App() {
  return <LanguageProvider><AppContent /></LanguageProvider>
}

function AppContent() {
  const { t } = useLanguage()
  const [profile, setProfile] = useState<StoreProfile | null>(() => {
    try {
      const saved = localStorage.getItem('bharatvoice.onboarding.v1')
      return saved ? JSON.parse(saved) as StoreProfile : null
    } catch { return null }
  })
  const [section, setSection] = useState<Section>('Overview')
  const [summary, setSummary] = useState<Summary | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [sales, setSales] = useState<Sale[]>([])
  const [search, setSearch] = useState('')
  const [apiOnline, setApiOnline] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [addProductOpen, setAddProductOpen] = useState(false)
  const [adjustingProduct, setAdjustingProduct] = useState<Product | null>(null)
  const [newBillOpen, setNewBillOpen] = useState(false)
  const [receiptSale, setReceiptSale] = useState<any | null>(null)
  const [mobileNav, setMobileNav] = useState(false)

  // Synchronize store profile with backend
  useEffect(() => {
    let active = true
    api.getStoreProfile().then(sp => {
      if (active && sp && sp.store_name) {
        setProfile(prev => {
          if (!prev) return prev
          if (prev.storeName !== sp.store_name || (sp.owner_name && prev.ownerName !== sp.owner_name)) {
            const next: StoreProfile = {
              ...prev,
              storeName: sp.store_name || prev.storeName,
              ownerName: sp.owner_name || prev.ownerName,
              mobile: sp.phone || prev.mobile,
              city: sp.address || prev.city,
            }
            localStorage.setItem('bharatvoice.onboarding.v1', JSON.stringify(next))
            return next
          }
          return prev
        })
      }
    }).catch(() => {})
    return () => { active = false }
  }, [])

  async function loadData() {
    setLoading(true)
    setError('')
    try {
      const [health, summaryData, productData, salesData, storeProfile] = await Promise.all([
        api.health(), api.summary(), api.products(), api.recentSales(), api.getStoreProfile().catch(() => null)
      ])
      setApiOnline(health.status === 'ok')
      setSummary(summaryData)
      setProducts(productData)
      setSales(salesData)
      if (storeProfile && storeProfile.store_name) {
        setProfile(prev => {
          if (!prev) return prev
          if (prev.storeName !== storeProfile.store_name || (storeProfile.owner_name && prev.ownerName !== storeProfile.owner_name)) {
            const next: StoreProfile = {
              ...prev,
              storeName: storeProfile.store_name || prev.storeName,
              ownerName: storeProfile.owner_name || prev.ownerName,
              mobile: storeProfile.phone || prev.mobile,
              city: storeProfile.address || prev.city,
            }
            localStorage.setItem('bharatvoice.onboarding.v1', JSON.stringify(next))
            return next
          }
          return prev
        })
      }
    } catch {
      setApiOnline(false)
      setError('Cannot connect to the backend. Start the FastAPI server, then refresh this page.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadData() }, [])

  function completeOnboarding(nextProfile: StoreProfile) {
    localStorage.setItem('bharatvoice.onboarding.v1', JSON.stringify(nextProfile))
    setProfile(nextProfile)
  }

  const lowStockProducts = useMemo(
    () => products.filter(p => p.stock_qty <= p.reorder_level).sort((a, b) => a.stock_qty - b.stock_qty),
    [products]
  )
  const filteredProducts = useMemo(() => {
    const term = search.toLowerCase().trim()
    return products.filter(p => `${p.name} ${p.sku} ${p.category}`.toLowerCase().includes(term))
  }, [products, search])

  if (!profile) return <Onboarding onComplete={completeOnboarding} />

  function exportProducts() {
    const header = 'SKU,Product,Category,Stock,Unit,Price,Cost price,Reorder level'
    const rows = filteredProducts.map(p =>
      [p.sku, p.name, p.category, p.stock_qty, p.unit, p.price, p.cost_price, p.reorder_level]
        .map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')
    )
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'bharatvoice-products.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="app-shell">
      {mobileNav && <button className="mobile-backdrop" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
      <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark"><AudioLines size={23} strokeWidth={2.4} /></div>
          <div><div className="brand-name">BharatVoice<span> AI</span></div><div className="brand-subtitle">RETAIL OPERATIONS</div></div>
          <button className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={18}/></button>
        </div>
        <div className="store-switcher" onClick={() => setSettingsOpen(true)} style={{ cursor: 'pointer' }} title="Click to edit store settings & rename">
          <div className="store-avatar">BV</div>
          <div className="store-copy"><strong>{profile.storeName}</strong><span>{profile.storeType}</span></div>
          <ChevronDown size={15} className="muted-icon"/>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="main-nav" aria-label="Main navigation">
          {navItems.map(item => {
            const Icon = item.icon
            return <button key={item.label} className={`nav-item ${section === item.label ? 'active' : ''}`} onClick={() => { setSection(item.label); setMobileNav(false) }}>
              <Icon size={18}/><span>{t(item.label)}</span>{item.label === 'Products & Stock' && lowStockProducts.length > 0 && <span className="nav-count">{lowStockProducts.length}</span>}
            </button>
          })}
        </nav>
        <div className="sidebar-spacer"/>
        <div className="voice-promo">
          <div className="voice-promo-icon"><AudioLines size={19}/></div>
          <strong>{t("Your store, your language.")}</strong>
          <p>Voice-powered retail intelligence & instant billing.</p>
          <button onClick={() => setVoiceOpen(true)}>Open voice assistant <ArrowUpRight size={14}/></button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <button className="nav-item bottom-nav" onClick={() => setSettingsOpen(true)}><Settings2 size={18}/><span>Store Settings</span></button>
          <button className={`nav-item bottom-nav ${section === 'Reports' ? 'active' : ''}`} onClick={() => setSection('Reports')}><ChartNoAxesCombined size={18}/><span>Reports & Analytics</span></button>
        </div>
        <div className="sidebar-footer"><span className={`status-dot ${apiOnline ? 'online' : ''}`}/>{apiOnline ? 'Connected to local database' : 'Backend disconnected'}<span className="version">MVP · 0.2</span></div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={20}/></button>
            <div className="breadcrumbs"><span>{t("Workspace")}</span><span className="crumb-slash">/</span><strong>{t(section)}</strong></div>
          </div>
          <div className="topbar-actions">
            <LanguageSelect />
            <div className="connection-pill"><span className={`status-dot ${apiOnline ? 'online' : ''}`}/>{apiOnline ? 'System online' : 'Connecting'}</div>
            <button className="icon-button notification-button" aria-label="Notifications"><Bell size={18}/><i/></button>
            <div className="profile-avatar" style={{ cursor: "pointer" }} onClick={() => setSettingsOpen(true)} title={`Click to edit settings · ${profile.ownerName} (${profile.storeName})`}>{profile.ownerName.charAt(0).toUpperCase()}</div>
          </div>
        </header>

        <div className="page-content">
          {error && <div className="error-banner"><AlertTriangle size={18}/><span>{error}</span><button onClick={() => void loadData()}>Retry</button></div>}
          {section === 'Overview' && <Overview
            summary={summary} products={products} lowStockProducts={lowStockProducts}
            sales={sales} loading={loading} onOpenProducts={() => setSection('Products & Stock')}
            onOpenSales={() => setSection('Bills & Sales')} onVoice={() => setVoiceOpen(true)}
            onOpenNewBill={() => setNewBillOpen(true)} onSelectSale={(s) => setReceiptSale(s)}
          />}
          {section === 'Products & Stock' && <ProductsPage
            products={filteredProducts} search={search} setSearch={setSearch}
            onExport={exportProducts} loading={loading}
            onOpenAddProduct={() => setAddProductOpen(true)}
            onAdjustStock={(p) => setAdjustingProduct(p)}
          />}
          {section === 'Bills & Sales' && <SalesPage
            sales={sales} loading={loading}
            onOpenNewBill={() => setNewBillOpen(true)}
            onSelectSale={(s) => setReceiptSale(s)}
          />}
          {section === 'Purchasing' && <ComingSoon title="Purchasing" description="Supplier records, purchase orders and goods-received workflows are next in the build plan." icon={<Truck size={23}/>}/>}
          {section === 'Customers & Staff' && <ComingSoon title="Customers & Staff" description="Customer history, team roles and permissions will be added after core retail workflows." icon={<Users size={23}/>}/>}
          {section === 'Reports' && <ReportsPage summary={summary} sales={sales}/>}
        </div>
      </main>

      {voiceOpen && <VoiceModal onClose={() => setVoiceOpen(false)} onDataChanged={() => void loadData()}/>}
      {addProductOpen && (
        <AddProductModal
          productsCount={products.length}
          onClose={() => setAddProductOpen(false)}
          onSuccess={() => { setAddProductOpen(false); void loadData() }}
        />
      )}
      {adjustingProduct && (
        <AdjustStockModal
          product={adjustingProduct}
          onClose={() => setAdjustingProduct(null)}
          onSuccess={() => { setAdjustingProduct(null); void loadData() }}
        />
      )}
      {newBillOpen && (
        <NewBillModal
          products={products}
          onClose={() => setNewBillOpen(false)}
          onSuccess={(created) => {
            setNewBillOpen(false)
            void loadData()
            setReceiptSale(created)
          }}
        />
      )}
      {receiptSale && (
        <ReceiptModal
          sale={receiptSale}
          profile={profile}
          onClose={() => setReceiptSale(null)}
          onRefunded={() => {
            setReceiptSale(null)
            void loadData()
          }}
        />
      )}
      {settingsOpen && (
        <SettingsModal
          profile={profile}
          onClose={() => setSettingsOpen(false)}
          onSave={(updatedProfile) => {
            setProfile(updatedProfile)
            setSettingsOpen(false)
          }}
          onResetOnboarding={() => {
            localStorage.removeItem('bharatvoice.onboarding.v1')
            setProfile(null)
            setSettingsOpen(false)
          }}
        />
      )}
    </div>
  )
}

function PageHeading({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: React.ReactNode }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{actions && <div className="heading-actions">{actions}</div>}</div>
}

function Overview({ summary, products, lowStockProducts, sales, loading, onOpenProducts, onOpenSales, onVoice, onOpenNewBill, onSelectSale }: {
  summary: Summary | null; products: Product[]; lowStockProducts: Product[]; sales: Sale[]; loading: boolean;
  onOpenProducts: () => void; onOpenSales: () => void; onVoice: () => void; onOpenNewBill: () => void; onSelectSale: (s: Sale) => void;
}) {
  const { t } = useLanguage()
  const metrics = [
    { label: t("Today's sales"), value: summary ? money(summary.today_sales) : '—', hint: 'Completed bills today', icon: Wallet, tone: 'green' },
    { label: t('Bills today'), value: summary ? String(summary.today_bills) : '—', hint: 'Completed transactions', icon: ReceiptText, tone: 'blue' },
    { label: t('Products listed'), value: summary ? String(summary.total_products) : '—', hint: 'Across your catalogue', icon: ShoppingBag, tone: 'violet' },
    { label: t('Low-stock items'), value: summary ? String(summary.low_stock) : '—', hint: 'Need attention', icon: AlertTriangle, tone: 'amber' },
  ]
  return <>
    <PageHeading eyebrow="STORE OVERVIEW" title="Good to see you." description="Here's what's happening in your store today."
      actions={<><button className="button button-secondary" onClick={onVoice}><Mic size={16}/> {t("Voice assistant")}</button><button className="button button-primary" onClick={onOpenNewBill}><Plus size={16}/> {t("New bill")}</button></>}/>
    <div className="date-strip"><CalendarDays size={15}/><span>{new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</span><span className="date-separator">·</span><span className="subtle">Live store summary</span><button onClick={() => window.location.reload()}><Activity size={14}/> {t("Refresh")}</button></div>
    <div className="metrics-grid">
      {metrics.map(m => { const Icon = m.icon; return <div className="metric-card" key={m.label}>
        <div className={`metric-icon ${m.tone}`}><Icon size={19}/></div><div className="metric-label">{m.label}</div>
        <div className="metric-value">{loading ? <span className="skeleton"/> : m.value}</div><div className="metric-hint">{m.hint}</div>
      </div>})}
    </div>
    <div className="overview-grid">
      <section className="panel sales-panel">
        <div className="panel-heading"><div><h2>{t("Sales activity")}</h2><p>Recorded sales over the last 7 days</p></div><span className="panel-chip"><span className="tiny-dot"/> From database</span></div>
        <div className="chart-summary"><strong>{money((summary?.sales_trend ?? []).reduce((sum, d) => sum + d.sales, 0))}</strong><span>Recorded in the displayed period</span></div>
        <div className="chart-wrap">
          {summary?.sales_trend?.length ? <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={summary.sales_trend} margin={{ top: 10, right: 6, left: -18, bottom: 0 }}>
              <defs><linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#24744d" stopOpacity={0.19}/><stop offset="95%" stopColor="#24744d" stopOpacity={0.01}/></linearGradient></defs>
              <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="#e9ede8"/>
              <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: '#8b948c', fontSize: 11 }} dy={10}/>
              <YAxis axisLine={false} tickLine={false} tick={{ fill: '#8b948c', fontSize: 11 }} tickFormatter={v => v >= 1000 ? `₹${v/1000}k` : `₹${v}`}/>
              <Tooltip formatter={(value) => [money(Number(value)), 'Sales']} contentStyle={{ borderRadius: 12, border: '1px solid #e7ebe6', fontSize: 12 }}/>
              <Area type="monotone" dataKey="sales" stroke="#24744d" strokeWidth={2.5} fill="url(#salesFill)" activeDot={{ r: 5, strokeWidth: 0 }}/>
            </AreaChart>
          </ResponsiveContainer> : <div className="empty-chart">Sales chart will appear when data is available.</div>}
        </div>
        <div className="panel-footer"><span>Demo records are clearly identified by their BV bill numbers.</span><button className="text-button" onClick={onOpenSales}>View sales <ArrowUpRight size={14}/></button></div>
      </section>
      <section className="panel stock-panel">
        <div className="panel-heading"><div><h2>{t("Stock to watch")}</h2><p>Items at or below reorder level</p></div><span className="alert-count">{lowStockProducts.length}</span></div>
        {lowStockProducts.length ? <div className="stock-list">{lowStockProducts.slice(0, 5).map(p => <div className="stock-row" key={p.id}>
          <div className="product-mini-icon"><Package size={17}/></div><div className="stock-product"><strong>{p.name}</strong><span>{p.sku} · Reorder at {p.reorder_level} {p.unit}</span></div><div className={`stock-qty ${p.stock_qty === 0 ? 'out' : ''}`}>{p.stock_qty}<small> {p.unit}</small></div>
        </div>)}</div> : <div className="empty-state"><CheckCircle2 size={22}/><span>No low-stock items.</span></div>}
        <div className="panel-footer"><span>Review before reordering</span><button className="text-button" onClick={onOpenProducts}>All products <ArrowUpRight size={14}/></button></div>
      </section>
    </div>
    <section className="panel recent-panel">
      <div className="panel-heading"><div><h2>{t("Recent bills")}</h2><p>Latest recorded sales transactions</p></div><button className="button button-secondary button-small" onClick={onOpenSales}>View all bills <ArrowUpRight size={14}/></button></div>
      <SalesTable sales={sales.slice(0, 5)} loading={loading} onSelectSale={onSelectSale}/>
    </section>
    <div className="dashboard-note"><Sparkles size={16}/><span><strong>Built for the way you work.</strong> Real-time voice commands, multilingual support, stock tracking, and POS billing are all active on this store.</span></div>
  </>
}

function ProductsPage({
  products, search, setSearch, onExport, loading, onOpenAddProduct, onAdjustStock
}: {
  products: Product[]; search: string; setSearch: (v: string) => void; onExport: () => void; loading: boolean;
  onOpenAddProduct: () => void; onAdjustStock: (p: Product) => void;
}) {
  const { t } = useLanguage()
  return <>
    <PageHeading
      eyebrow="CATALOGUE & INVENTORY"
      title="Products & stock"
      description="Search your catalogue and review stock levels."
      actions={
        <>
          <button className="button button-primary" onClick={onOpenAddProduct}><Plus size={16}/> {t("Add Product")}</button>
          <button className="button button-secondary" onClick={onExport}><Download size={16}/> Export CSV</button>
        </>
      }
    />
    <section className="panel">
      <div className="table-toolbar"><div className="search-box"><Search size={17}/><input aria-label="Search products" placeholder="Search product, SKU or category..." value={search} onChange={e => setSearch(e.target.value)}/>{search && <button className="clear-search" onClick={() => setSearch('')} aria-label="Clear search"><X size={14}/></button>}</div><span className="result-count">{products.length} products</span></div>
      <div className="table-scroll"><table><thead><tr><th>Product</th><th>SKU</th><th>Category</th><th>{t("In stock")}</th><th>Unit price</th><th>Stock status</th><th className="align-right">Action</th></tr></thead><tbody>
        {products.map(p => <tr key={p.id}>
          <td><div className="table-product"><div className="table-product-icon"><Package size={17}/></div><strong>{p.name}</strong></div></td>
          <td className="mono">{p.sku}</td>
          <td>{p.category}</td>
          <td><strong>{p.stock_qty}</strong> <span className="subtle">{p.unit}</span></td>
          <td>{money(p.price)}</td>
          <td>{p.stock_qty <= p.reorder_level ? <span className={`status-tag ${p.stock_qty === 0 ? 'danger' : 'warning'}`}>{p.stock_qty === 0 ? 'Out of stock' : 'Low stock'}</span> : <span className="status-tag healthy">{t("In stock")}</span>}</td>
          <td className="align-right">
            <button className="table-action-btn" onClick={() => onAdjustStock(p)}><Boxes size={12}/> Adjust</button>
          </td>
        </tr>)}
        {!loading && products.length === 0 && <tr><td colSpan={7} className="empty-table">No products match your search.</td></tr>}
      </tbody></table></div>
    </section>
  </>
}

function SalesTable({ sales, loading, onSelectSale }: { sales: Sale[]; loading: boolean; onSelectSale?: (sale: Sale) => void }) {
  if (loading) return <div className="table-loading">Loading recent transactions…</div>
  if (!sales.length) return <div className="empty-table">No sales recorded yet.</div>
  return <div className="table-scroll"><table><thead><tr><th>Bill number</th><th>Customer</th><th>Payment</th><th>Status</th><th className="align-right">Amount</th><th>Date</th>{onSelectSale && <th className="align-right">Receipt</th>}</tr></thead><tbody>
    {sales.map(s => <tr key={s.id}>
      <td className="mono bill-id">{s.bill_number}</td>
      <td>{s.customer_name || 'Walk-in Customer'}</td>
      <td><span className="payment-method">{s.payment_method}</span></td>
      <td><span className={`status-tag ${s.status === 'Completed' ? 'healthy' : 'danger'}`}>{s.status}</span></td>
      <td className="align-right amount-cell">{money(s.total)}</td>
      <td className="date-cell">{new Date(s.created_at + (s.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}</td>
      {onSelectSale && (
        <td className="align-right">
          <button className="table-action-btn" onClick={() => onSelectSale(s)}><Printer size={12}/> View</button>
        </td>
      )}
    </tr>)}
  </tbody></table></div>
}

function SalesPage({ sales, loading, onOpenNewBill, onSelectSale }: { sales: Sale[]; loading: boolean; onOpenNewBill: () => void; onSelectSale: (s: Sale) => void }) {
  const { t } = useLanguage()

  function exportSalesCSV() {
    if (!sales.length) {
      alert('No sales bills recorded yet.')
      return
    }
    const headers = ['Bill Number', 'Customer Name', 'Payment Method', 'Status', 'Total (INR)', 'Date & Time']
    const rows = sales.map(s => [
      s.bill_number,
      `"${(s.customer_name || 'Walk-in Customer').replace(/"/g, '""')}"`,
      s.payment_method,
      s.status,
      s.total.toFixed(2),
      `"${new Date(s.created_at + (s.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString('en-IN')}"`
    ])
    const csvContent = 'data:text/csv;charset=utf-8,\uFEFF' + [headers.join(','), ...rows.map(r => r.join(','))].join('\r\n')
    const encodedUri = encodeURI(csvContent)
    const link = document.createElement('a')
    link.setAttribute('href', encodedUri)
    link.setAttribute('download', `bharatvoice-sales-bills-${new Date().toISOString().slice(0, 10)}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  return <>
    <PageHeading
      eyebrow="TRANSACTIONS"
      title="Bills & sales"
      description="Review recorded transactions, receipts, and payment methods."
      actions={
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="button button-secondary" onClick={exportSalesCSV}>
            <Download size={16}/> {t("Export CSV")}
          </button>
          <button className="button button-primary" onClick={onOpenNewBill}>
            <Plus size={16}/> {t("New bill")}
          </button>
        </div>
      }
    />
    <section className="panel">
      <div className="panel-heading"><div><h2>Recent transactions</h2><p>Most recent bills from the local database</p></div></div>
      <SalesTable sales={sales} loading={loading} onSelectSale={onSelectSale}/>
    </section>
  </>
}

function ReportsPage({ summary, sales }: { summary: Summary | null; sales: Sale[] }) {
  const { t } = useLanguage()
  const [metrics, setMetrics] = useState<any>(null)
  const [valuation, setValuation] = useState<any>(null)

  useEffect(() => {
    let active = true
    async function fetchFinancials() {
      try {
        const [m, v] = await Promise.all([api.financeMetrics('today'), api.financeValuation()])
        if (active) {
          setMetrics(m)
          setValuation(v)
        }
      } catch {
        // Fallback to basic summary
      }
    }
    void fetchFinancials()
    return () => { active = false }
  }, [])

  return (
    <>
      <PageHeading eyebrow="BUSINESS INTELLIGENCE" title="Reports & Financials" description="Live financial intelligence, margins, and inventory valuation from your store." />
      <div className="metrics-grid report-metrics">
        <div className="metric-card">
          <div className="metric-icon green"><Wallet size={19}/></div>
          <div className="metric-label">{t("Today's sales")}</div>
          <div className="metric-value">{money(metrics?.net_sales ?? summary?.today_sales ?? 0)}</div>
          <div className="metric-hint">Net revenue today</div>
        </div>
        <div className="metric-card">
          <div className="metric-icon blue"><ReceiptText size={19}/></div>
          <div className="metric-label">Gross Profit</div>
          <div className="metric-value">{money(metrics?.gross_profit ?? 0)}</div>
          <div className="metric-hint">Margin: {metrics?.gross_profit_margin ?? 0}%</div>
        </div>
        <div className="metric-card">
          <div className="metric-icon violet"><ChartNoAxesCombined size={19}/></div>
          <div className="metric-label">Net Profit</div>
          <div className="metric-value">{money(metrics?.net_profit ?? 0)}</div>
          <div className="metric-hint">After expenses ({metrics?.net_profit_margin ?? 0}%)</div>
        </div>
        <div className="metric-card">
          <div className="metric-icon amber"><Boxes size={19}/></div>
          <div className="metric-label">Stock Valuation (Retail)</div>
          <div className="metric-value">{money(valuation?.total_selling_valuation ?? summary?.inventory_value ?? 0)}</div>
          <div className="metric-hint">Cost: {money(valuation?.total_cost_valuation ?? 0)}</div>
        </div>
      </div>

      <div className="overview-grid" style={{ marginTop: 20 }}>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Financial Breakdown</h2>
              <p>Income, cost of goods, and operating expenses today</p>
            </div>
          </div>
          <div style={{ padding: '8px 0', fontSize: 13 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Gross Sales:</span><strong>{money(metrics?.gross_sales ?? 0)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Discounts Given:</span><strong style={{ color: '#a4382c' }}>- {money(metrics?.discounts ?? 0)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Cost of Goods Sold (COGS):</span><strong style={{ color: '#8c5112' }}>{money(metrics?.cogs ?? 0)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Operating Expenses:</span><strong style={{ color: '#a4382c' }}>{money(metrics?.operating_expenses ?? 0)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0', fontSize: 15, fontWeight: 'bold', color: '#165e38' }}>
              <span>Net Business Profit:</span><span>{money(metrics?.net_profit ?? 0)}</span>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Catalogue Valuation</h2>
              <p>Aggregate stock levels and asset valuation</p>
            </div>
          </div>
          <div style={{ padding: '8px 0', fontSize: 13 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Products in Catalogue:</span><strong>{valuation?.total_items_count ?? summary?.total_products ?? 0} items</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Total Units in Stock:</span><strong>{valuation?.total_stock_units ?? 0} units</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Inventory Cost Basis:</span><strong>{money(valuation?.total_cost_valuation ?? 0)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid #edf1eb' }}>
              <span>Estimated Retail Value:</span><strong>{money(valuation?.total_selling_valuation ?? 0)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0', fontSize: 15, fontWeight: 'bold', color: '#1f643e' }}>
              <span>Potential Retail Margin:</span><span>{valuation?.total_selling_valuation ? money(valuation.total_selling_valuation - valuation.total_cost_valuation) : '₹0'}</span>
            </div>
          </div>
        </section>
      </div>
    </>
  )
}

function ComingSoon({ title, description, icon }: { title: string; description: string; icon: React.ReactNode }) {
  return <><PageHeading eyebrow="STORE OPERATIONS" title={title} description={description}/><section className="coming-soon"><div className="coming-icon">{icon}</div><h2>This module is planned</h2><p>We're building the core catalogue, inventory and billing foundation first so future modules can use consistent, reliable data.</p></section></>
}

/* Modals */

function AddProductModal({
  productsCount,
  onClose,
  onSuccess
}: {
  productsCount: number
  onClose: () => void
  onSuccess: () => void
}) {
  const { t } = useLanguage()
  const defaultSku = `BV-${1000 + productsCount + 1}`
  const [sku, setSku] = useState(defaultSku)
  const [name, setName] = useState('')
  const [category, setCategory] = useState('General')
  const [unit, setUnit] = useState('pack')
  const [price, setPrice] = useState('100')
  const [costPrice, setCostPrice] = useState('80')
  const [stockQty, setStockQty] = useState('10')
  const [reorderLevel, setReorderLevel] = useState('5')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { setError('Product name is required'); return }
    const p = parseFloat(price)
    const cp = parseFloat(costPrice)
    const sq = parseFloat(stockQty)
    const rl = parseFloat(reorderLevel)
    if (isNaN(p) || p <= 0) { setError('Price must be greater than 0'); return }
    if (isNaN(cp) || cp < 0) { setError('Cost price cannot be negative'); return }
    if (isNaN(sq) || sq < 0) { setError('Stock cannot be negative'); return }

    setSubmitting(true)
    setError('')
    try {
      await api.createProduct({
        sku: sku.trim() || defaultSku,
        name: name.trim(),
        category: category.trim() || 'General',
        unit: unit.trim() || 'pack',
        price: p,
        cost_price: cp,
        stock_qty: sq,
        reorder_level: isNaN(rl) ? 5 : rl
      })
      onSuccess()
    } catch (err: any) {
      setError(err?.message || 'Failed to create product.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="standard-modal" role="dialog" aria-modal="true">
        <div className="modal-header">
          <div>
            <div className="eyebrow">CATALOGUE MANAGEMENT</div>
            <h2>{t('Add New Product')}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button>
        </div>
        {error && <div className="error-banner" style={{ marginBottom: 15 }}><AlertTriangle size={16}/><span>{error}</span></div>}
        <form onSubmit={handleSubmit}>
          <div className="form-grid">
            <div className="form-group">
              <label className="form-label">SKU</label>
              <input className="form-input mono" value={sku} onChange={e => setSku(e.target.value)} required />
            </div>
            <div className="form-group">
              <label className="form-label">{t('Category')}</label>
              <select className="form-select" value={category} onChange={e => setCategory(e.target.value)}>
                <option value="General">General</option>
                <option value="Grocery">Grocery</option>
                <option value="Staples">Staples</option>
                <option value="Dairy">Dairy</option>
                <option value="Snacks">Snacks</option>
                <option value="Spices">Spices</option>
                <option value="Beverages">Beverages</option>
                <option value="Personal Care">Personal Care</option>
                <option value="Household">Household</option>
              </select>
            </div>
            <div className="form-group" style={{ gridColumn: 'span 2' }}>
              <label className="form-label">{t('Product')} Name</label>
              <input className="form-input" placeholder="e.g. Basmati Rice 1kg" value={name} onChange={e => setName(e.target.value)} required />
            </div>
            <div className="form-group">
              <label className="form-label">{t('Unit price')} (₹)</label>
              <input type="number" step="0.5" className="form-input" value={price} onChange={e => setPrice(e.target.value)} required />
            </div>
            <div className="form-group">
              <label className="form-label">Cost Price (₹)</label>
              <input type="number" step="0.5" className="form-input" value={costPrice} onChange={e => setCostPrice(e.target.value)} required />
            </div>
            <div className="form-group">
              <label className="form-label">Initial Stock</label>
              <input type="number" step="1" className="form-input" value={stockQty} onChange={e => setStockQty(e.target.value)} required />
            </div>
            <div className="form-group">
              <label className="form-label">Unit</label>
              <select className="form-select" value={unit} onChange={e => setUnit(e.target.value)}>
                <option value="pack">pack</option>
                <option value="kg">kg</option>
                <option value="g">g</option>
                <option value="l">l</option>
                <option value="ml">ml</option>
                <option value="piece">piece</option>
                <option value="bottle">bottle</option>
              </select>
            </div>
            <div className="form-group" style={{ gridColumn: 'span 2' }}>
              <label className="form-label">Reorder Alert Level</label>
              <input type="number" step="1" className="form-input" value={reorderLevel} onChange={e => setReorderLevel(e.target.value)} />
            </div>
          </div>
          <div className="modal-actions">
            <button type="button" className="button button-secondary" onClick={onClose}>{t('Cancel')}</button>
            <button type="submit" className="button button-primary" disabled={submitting}>
              {submitting ? 'Adding…' : t('Add Product')}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

function AdjustStockModal({
  product,
  onClose,
  onSuccess
}: {
  product: Product
  onClose: () => void
  onSuccess: () => void
}) {
  const { t } = useLanguage()
  const [direction, setDirection] = useState<'ADD' | 'DEDUCT'>('ADD')
  const [quantity, setQuantity] = useState('5')
  const [reason, setReason] = useState('MANUAL_ADJUSTMENT')
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const qty = parseFloat(quantity)
    if (isNaN(qty) || qty <= 0) { setError('Quantity must be greater than 0'); return }
    const change = direction === 'ADD' ? qty : -qty
    if (direction === 'DEDUCT' && product.stock_qty - qty < 0) {
      setError(`Cannot deduct: Available stock is only ${product.stock_qty} ${product.unit}`)
      return
    }

    setSubmitting(true)
    setError('')
    try {
      await api.adjustStock(product.id, change, reason, notes)
      onSuccess()
    } catch (err: any) {
      setError(err?.message || 'Failed to adjust stock.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="standard-modal" role="dialog" aria-modal="true">
        <div className="modal-header">
          <div>
            <div className="eyebrow">INVENTORY CONTROL</div>
            <h2>Adjust Stock: {product.name}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button>
        </div>
        <div style={{ marginBottom: 15, padding: '10px 14px', background: '#f6f9f5', borderRadius: 9, fontSize: 12 }}>
          SKU: <strong>{product.sku}</strong> · Current stock: <strong>{product.stock_qty} {product.unit}</strong>
        </div>
        {error && <div className="error-banner" style={{ marginBottom: 15 }}><AlertTriangle size={16}/><span>{error}</span></div>}
        <form onSubmit={handleSubmit}>
          <div className="form-grid">
            <div className="form-group">
              <label className="form-label">Adjustment Type</label>
              <select className="form-select" value={direction} onChange={e => setDirection(e.target.value as any)}>
                <option value="ADD">Add Stock (+)</option>
                <option value="DEDUCT">Deduct Stock (-)</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Quantity ({product.unit})</label>
              <input type="number" step="0.5" className="form-input" value={quantity} onChange={e => setQuantity(e.target.value)} required />
            </div>
            <div className="form-group" style={{ gridColumn: 'span 2' }}>
              <label className="form-label">Reason</label>
              <select className="form-select" value={reason} onChange={e => setReason(e.target.value)}>
                <option value="PURCHASE_RECEIPT">Supplier Delivery / Purchase</option>
                <option value="MANUAL_ADJUSTMENT">Manual Stock Correction</option>
                <option value="DAMAGED_EXPIRED">Damaged / Expired Goods</option>
                <option value="CUSTOMER_RETURN">Customer Return</option>
              </select>
            </div>
            <div className="form-group" style={{ gridColumn: 'span 2' }}>
              <label className="form-label">Notes (Optional)</label>
              <input className="form-input" placeholder="e.g. Physical inventory count verified" value={notes} onChange={e => setNotes(e.target.value)} />
            </div>
          </div>
          <div className="modal-actions">
            <button type="button" className="button button-secondary" onClick={onClose}>{t('Cancel')}</button>
            <button type="submit" className="button button-primary" disabled={submitting}>
              {submitting ? 'Saving…' : 'Save Adjustment'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

function NewBillModal({
  products,
  onClose,
  onSuccess
}: {
  products: Product[]
  onClose: () => void
  onSuccess: (sale: any) => void
}) {
  const { t } = useLanguage()
  const [customerName, setCustomerName] = useState('Walk-in Customer')
  const [paymentMethod, setPaymentMethod] = useState('Cash')
  const [selectedProductId, setSelectedProductId] = useState<number>(products[0]?.id || 0)
  const [selectedQty, setSelectedQty] = useState('1')
  const [cart, setCart] = useState<{ product: Product; quantity: number }[]>([])
  const [discount, setDiscount] = useState('0')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  function handleAddToCart() {
    const prod = products.find(p => p.id === Number(selectedProductId))
    if (!prod) return
    const qty = parseFloat(selectedQty)
    if (isNaN(qty) || qty <= 0) { setError('Quantity must be greater than 0'); return }
    if (qty > prod.stock_qty) {
      setError(`Cannot add ${qty} ${prod.unit}. Only ${prod.stock_qty} available in stock.`)
      return
    }

    setError('')
    setCart(prev => {
      const idx = prev.findIndex(item => item.product.id === prod.id)
      if (idx >= 0) {
        const nextQty = prev[idx].quantity + qty
        if (nextQty > prod.stock_qty) {
          setError(`Cannot add: Total quantity (${nextQty}) exceeds stock (${prod.stock_qty}).`)
          return prev
        }
        const updated = [...prev]
        updated[idx] = { ...updated[idx], quantity: nextQty }
        return updated
      }
      return [...prev, { product: prod, quantity: qty }]
    })
    setSelectedQty('1')
  }

  function handleRemoveItem(idx: number) {
    setCart(prev => prev.filter((_, i) => i !== idx))
  }

  const subtotal = cart.reduce((sum, item) => sum + item.product.price * item.quantity, 0)
  const discNum = Math.max(0, parseFloat(discount) || 0)
  const grandTotal = Math.max(0, subtotal - discNum)

  async function handleCheckout(e: React.FormEvent) {
    e.preventDefault()
    if (!cart.length) { setError('Cart is empty. Please add items to checkout.'); return }

    setSubmitting(true)
    setError('')
    try {
      const payload = {
        customer_name: customerName.trim() || 'Walk-in Customer',
        payment_method: paymentMethod,
        discount: discNum,
        items: cart.map(item => ({
          sku: item.product.sku,
          quantity: item.quantity,
          unit_price: item.product.price,
        }))
      }
      const created = await api.createSale(payload)
      onSuccess(created)
    } catch (err: any) {
      setError(err?.message || 'Failed to complete sale.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="standard-modal" style={{ width: 'min(100%, 620px)' }} role="dialog" aria-modal="true">
        <div className="modal-header">
          <div>
            <div className="eyebrow">POINT OF SALE</div>
            <h2>{t('New bill')}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button>
        </div>
        {error && <div className="error-banner" style={{ marginBottom: 15 }}><AlertTriangle size={16}/><span>{error}</span></div>}

        <div className="form-grid" style={{ marginBottom: 15 }}>
          <div className="form-group">
            <label className="form-label">{t('Customer')}</label>
            <input className="form-input" value={customerName} onChange={e => setCustomerName(e.target.value)} />
          </div>
          <div className="form-group">
            <label className="form-label">{t('Payment')}</label>
            <select className="form-select" value={paymentMethod} onChange={e => setPaymentMethod(e.target.value)}>
              <option value="Cash">Cash</option>
              <option value="UPI">UPI / QR Code</option>
              <option value="Card">Card</option>
              <option value="Store Credit">Store Credit / Khata</option>
            </select>
          </div>
        </div>

        <div style={{ background: '#f8faf7', border: '1px solid #e7ede6', borderRadius: 12, padding: 14, marginBottom: 15 }}>
          <div className="eyebrow" style={{ marginBottom: 8 }}>SELECT ITEM</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select
              className="form-select"
              style={{ flex: 2 }}
              value={selectedProductId}
              onChange={e => setSelectedProductId(Number(e.target.value))}
            >
              {products.map(p => (
                <option key={p.id} value={p.id} disabled={p.stock_qty <= 0}>
                  {p.name} — ₹{p.price} ({p.stock_qty > 0 ? `${p.stock_qty} ${p.unit}` : 'Out of stock'})
                </option>
              ))}
            </select>
            <input
              type="number"
              step="0.5"
              min="0.5"
              className="form-input"
              style={{ width: 80 }}
              value={selectedQty}
              onChange={e => setSelectedQty(e.target.value)}
            />
            <button type="button" className="button button-secondary" onClick={handleAddToCart}>
              <Plus size={15}/> Add
            </button>
          </div>
        </div>

        {cart.length > 0 ? (
          <table className="pos-cart-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Rate</th>
                <th>Qty</th>
                <th className="align-right">Total</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {cart.map((item, idx) => (
                <tr key={item.product.id}>
                  <td><strong>{item.product.name}</strong><br/><small className="subtle">{item.product.sku}</small></td>
                  <td>₹{item.product.price}</td>
                  <td>{item.quantity} {item.product.unit}</td>
                  <td className="align-right">₹{(item.product.price * item.quantity).toFixed(2)}</td>
                  <td style={{ width: 30 }}>
                    <button type="button" className="icon-button" onClick={() => handleRemoveItem(idx)} style={{ color: '#a4382c' }}>
                      <Trash2 size={14}/>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div style={{ textAlign: 'center', padding: '20px 0', color: '#7a887d', fontSize: 12 }}>
            No items added yet. Select a product above and click Add.
          </div>
        )}

        <div style={{ borderTop: '1px solid #edf1eb', paddingTop: 12, marginTop: 12 }}>
          <div className="pos-total-row"><span>Subtotal:</span><span>₹{subtotal.toFixed(2)}</span></div>
          <div className="pos-total-row" style={{ alignItems: 'center' }}>
            <span>Discount (₹):</span>
            <input
              type="number"
              step="1"
              min="0"
              style={{ width: 80, height: 28, textAlign: 'right', border: '1px solid #d9e2d8', borderRadius: 6, padding: '0 6px' }}
              value={discount}
              onChange={e => setDiscount(e.target.value)}
            />
          </div>
          <div className="pos-total-row" style={{ fontSize: 16, color: '#165e38', borderTop: '2px solid #e1ebe0', paddingTop: 8 }}>
            <span>Grand Total:</span><span>₹{grandTotal.toFixed(2)}</span>
          </div>
        </div>

        <div className="modal-actions">
          <button type="button" className="button button-secondary" onClick={onClose}>{t('Cancel')}</button>
          <button type="button" className="button button-primary" disabled={submitting || !cart.length} onClick={handleCheckout}>
            {submitting ? 'Processing…' : `Complete Sale (₹${grandTotal.toFixed(2)})`}
          </button>
        </div>
      </section>
    </div>
  )
}

function ReceiptModal({
  sale,
  profile,
  onClose,
  onRefunded
}: {
  sale: any
  profile: StoreProfile
  onClose: () => void
  onRefunded: () => void
}) {
  const { t } = useLanguage()
  const [refunding, setRefunding] = useState(false)
  const [error, setError] = useState('')

  async function handleRefund() {
    if (!confirm('Are you sure you want to refund this bill? Inventory will be restored and accounts updated.')) return
    setRefunding(true)
    setError('')
    try {
      await api.refundSale(sale.id, 'Customer refund')
      onRefunded()
    } catch (err: any) {
      setError(err?.message || 'Failed to refund sale.')
    } finally {
      setRefunding(false)
    }
  }

  function downloadBill() {
    const items = sale.items || []
    const lines = [
      '==================================================',
      '               TAX INVOICE & RECEIPT              ',
      `                 ${profile.storeName}             `,
      `              ${profile.city} · ${profile.storeType} `,
      `                Contact: ${profile.mobile}         `,
      '==================================================',
      `Bill Number  : ${sale.bill_number}`,
      `Date & Time  : ${new Date(sale.created_at || Date.now()).toLocaleString('en-IN')}`,
      `Customer     : ${sale.customer_name || 'Walk-in Customer'}`,
      `Payment Mode : ${sale.payment_method}`,
      `Bill Status  : ${sale.status}`,
      '--------------------------------------------------',
      'Item Name                       Qty    Rate     Amount',
      '--------------------------------------------------',
    ]

    if (items.length > 0) {
      items.forEach((it: any) => {
        const name = (it.product_name || 'Item').padEnd(30).slice(0, 30)
        const qty = String(it.quantity).padStart(4)
        const rate = `₹${Number(it.unit_price).toFixed(0)}`.padStart(8)
        const amt = `₹${Number(it.line_total || it.quantity * it.unit_price).toFixed(2)}`.padStart(10)
        lines.push(`${name} ${qty} ${rate} ${amt}`)
      })
    } else {
      lines.push(`Total Transaction Recorded                ₹${Number(sale.total).toFixed(2)}`)
    }

    lines.push('--------------------------------------------------')
    lines.push(`TOTAL AMOUNT PAID :                     ₹${Number(sale.total).toFixed(2)}`)
    lines.push('==================================================')
    lines.push('       Thank you for shopping with us!            ')
    lines.push('            Please visit again.                   ')
    lines.push('          Powered by BharatVoice AI               ')
    lines.push('==================================================')

    const blob = new Blob([lines.join('\r\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `Invoice-${sale.bill_number}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const items = sale.items || []

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="standard-modal" style={{ width: 'min(100%, 460px)' }} role="dialog" aria-modal="true">
        <div className="modal-header">
          <div>
            <div className="eyebrow">TAX INVOICE & RECEIPT</div>
            <h2>Bill {sale.bill_number}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button>
        </div>
        {error && <div className="error-banner" style={{ marginBottom: 15 }}><AlertTriangle size={16}/><span>{error}</span></div>}

        <div className="receipt-box">
          <h3>{profile.storeName}</h3>
          <div style={{ textAlign: 'center', fontSize: 11, color: '#555' }}>
            {profile.city} · {profile.storeType}<br/>
            Contact: {profile.mobile}
          </div>
          <div className="receipt-divider"/>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
            <span>Bill: <strong>{sale.bill_number}</strong></span>
            <span>{new Date(sale.created_at || Date.now()).toLocaleDateString('en-IN')}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginTop: 4 }}>
            <span>Customer: {sale.customer_name || 'Walk-in Customer'}</span>
            <span>Payment: {sale.payment_method}</span>
          </div>
          <div style={{ fontSize: 11, marginTop: 4 }}>
            Status: <strong style={{ color: sale.status === 'Completed' ? '#1f603c' : '#a4382c' }}>{sale.status}</strong>
          </div>
          <div className="receipt-divider"/>

          {items.length > 0 ? (
            <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px dashed #ccc' }}>
                  <th style={{ textAlign: 'left', padding: '4px 0' }}>Item</th>
                  <th style={{ textAlign: 'center', padding: '4px 0' }}>Qty</th>
                  <th style={{ textAlign: 'right', padding: '4px 0' }}>Rate</th>
                  <th style={{ textAlign: 'right', padding: '4px 0' }}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it: any, idx: number) => (
                  <tr key={idx}>
                    <td style={{ padding: '4px 0' }}>{it.product_name}</td>
                    <td style={{ textAlign: 'center', padding: '4px 0' }}>{it.quantity}</td>
                    <td style={{ textAlign: 'right', padding: '4px 0' }}>₹{it.unit_price}</td>
                    <td style={{ textAlign: 'right', padding: '4px 0' }}>₹{(it.line_total || it.quantity * it.unit_price).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div style={{ fontSize: 11, padding: '6px 0' }}>Total recorded transaction: ₹{sale.total}</div>
          )}

          <div className="receipt-divider"/>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 'bold' }}>
            <span>TOTAL PAID:</span>
            <span>₹{Number(sale.total).toFixed(2)}</span>
          </div>
          <div className="receipt-divider"/>
          <div style={{ textAlign: 'center', fontSize: 10, color: '#777' }}>
            Thank you for shopping with us! Please visit again.<br/>
            Powered by BharatVoice AI
          </div>
        </div>

        <div className="modal-actions" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          {sale.status === 'Completed' ? (
            <button type="button" className="button button-secondary" style={{ color: '#a4382c' }} onClick={handleRefund} disabled={refunding}>
              <RotateCcw size={14}/> {refunding ? 'Refunding…' : 'Refund Bill'}
            </button>
          ) : <div/>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="button button-secondary" onClick={downloadBill} title="Download Bill Invoice file">
              <Download size={15}/> Export Bill
            </button>
            <button type="button" className="button button-secondary" onClick={() => window.print()} title="Print receipt">
              <Printer size={15}/> Print
            </button>
            <button type="button" className="button button-primary" onClick={onClose}>
              {t('Got it')}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}

function SettingsModal({
  profile,
  onClose,
  onSave,
  onResetOnboarding
}: {
  profile: StoreProfile
  onClose: () => void
  onSave: (p: StoreProfile) => void
  onResetOnboarding: () => void
}) {
  const { t } = useLanguage()
  const [storeName, setStoreName] = useState(profile.storeName)
  const [storeType, setStoreType] = useState(profile.storeType || 'Kirana / Grocery')
  const [ownerName, setOwnerName] = useState(profile.ownerName)
  const [mobile, setMobile] = useState(profile.mobile)
  const [city, setCity] = useState(profile.city)
  const [gstin, setGstin] = useState('')
  const [address, setAddress] = useState('')
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState('')

  // Load backend profile data on mount
  useEffect(() => {
    let active = true
    api.getStoreProfile().then(sp => {
      if (active && sp) {
        if (sp.store_name) setStoreName(sp.store_name)
        if (sp.owner_name) setOwnerName(sp.owner_name)
        if (sp.phone) setMobile(sp.phone)
        if (sp.gstin) setGstin(sp.gstin)
        if (sp.address) setAddress(sp.address)
      }
    }).catch(() => {})
    return () => { active = false }
  }, [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!storeName.trim()) {
      setError('Store name cannot be empty.')
      return
    }
    setSaving(true)
    setError('')
    try {
      // 1. Update backend store profile
      await api.updateStoreProfile({
        store_name: storeName.trim(),
        owner_name: ownerName.trim(),
        phone: mobile.trim(),
        address: address.trim() || city.trim(),
        gstin: gstin.trim() || undefined,
      })

      // 2. Build updated StoreProfile
      const updatedProfile: StoreProfile = {
        ...profile,
        storeName: storeName.trim(),
        storeType,
        ownerName: ownerName.trim(),
        mobile: mobile.trim(),
        city: city.trim(),
      }

      // 3. Persist to localStorage
      localStorage.setItem('bharatvoice.onboarding.v1', JSON.stringify(updatedProfile))

      setSuccess(true)
      setTimeout(() => {
        onSave(updatedProfile)
      }, 700)
    } catch (err: any) {
      setError(err?.message || 'Failed to save store settings.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="standard-modal" style={{ width: 'min(100%, 520px)' }} role="dialog" aria-modal="true">
        <div className="modal-header">
          <div>
            <div className="eyebrow">STORE CONFIGURATION & IDENTITY</div>
            <h2>Store & User Settings</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button>
        </div>

        {error && <div className="error-banner" style={{ marginBottom: 12 }}><AlertTriangle size={16}/><span>{error}</span></div>}
        {success && <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, background: '#eaf4ee', color: '#1f603c', padding: '8px 12px', borderRadius: 8, fontSize: 13 }}><CheckCircle2 size={16}/><span>Store settings updated successfully!</span></div>}

        <form onSubmit={handleSave} className="modal-form">
          <div className="field-group">
            <label className="field-label" htmlFor="settings-store-name">Store Name (Rename Store) *</label>
            <input
              id="settings-store-name"
              className="field-input"
              value={storeName}
              onChange={e => setStoreName(e.target.value)}
              placeholder="e.g. Sri Lakshmi Stores"
              required
            />
            <small style={{ color: '#68776b', fontSize: 11, marginTop: 4, display: 'block' }}>This name appears on the dashboard header, receipts, tax invoices, and reports.</small>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div className="field-group">
              <label className="field-label" htmlFor="settings-owner-name">Owner / Manager Name</label>
              <input
                id="settings-owner-name"
                className="field-input"
                value={ownerName}
                onChange={e => setOwnerName(e.target.value)}
                placeholder="Owner name"
              />
            </div>
            <div className="field-group">
              <label className="field-label" htmlFor="settings-store-type">Business Type</label>
              <select
                id="settings-store-type"
                className="field-input"
                value={storeType}
                onChange={e => setStoreType(e.target.value)}
              >
                <option value="Grocery / Kirana">Grocery / Kirana</option>
                <option value="Supermarket">Supermarket</option>
                <option value="Pharmacy / Chemist">Pharmacy / Chemist</option>
                <option value="Apparel & Garments">Apparel & Garments</option>
                <option value="Electronics & Hardware">Electronics & Hardware</option>
                <option value="Bakery & Sweets">Bakery & Sweets</option>
                <option value="Stationery & Books">Stationery & Books</option>
                <option value="General Retail">General Retail</option>
              </select>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div className="field-group">
              <label className="field-label" htmlFor="settings-mobile">Contact Phone / Mobile</label>
              <input
                id="settings-mobile"
                className="field-input"
                value={mobile}
                onChange={e => setMobile(e.target.value)}
                placeholder="10-digit number"
              />
            </div>
            <div className="field-group">
              <label className="field-label" htmlFor="settings-city">City / Town</label>
              <input
                id="settings-city"
                className="field-input"
                value={city}
                onChange={e => setCity(e.target.value)}
                placeholder="e.g. Hyderabad"
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div className="field-group">
              <label className="field-label" htmlFor="settings-address">Store Address / Location</label>
              <input
                id="settings-address"
                className="field-input"
                value={address}
                onChange={e => setAddress(e.target.value)}
                placeholder="Shop number, street"
              />
            </div>
            <div className="field-group">
              <label className="field-label" htmlFor="settings-gstin">GSTIN / Tax ID (Optional)</label>
              <input
                id="settings-gstin"
                className="field-input"
                value={gstin}
                onChange={e => setGstin(e.target.value.toUpperCase())}
                placeholder="e.g. 36AAACB1234F1Z5"
              />
            </div>
          </div>

          <div className="modal-actions" style={{ justifyContent: 'space-between', marginTop: 10 }}>
            <button
              type="button"
              className="button button-secondary"
              style={{ fontSize: 12, color: '#888' }}
              onClick={() => {
                if (confirm('Re-run store onboarding setup wizard from scratch?')) {
                  onResetOnboarding()
                }
              }}
            >
              Reset Setup
            </button>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" className="button button-secondary" onClick={onClose}>
                {t('Cancel')}
              </button>
              <button type="submit" className="button button-primary" disabled={saving}>
                {saving ? 'Saving…' : 'Save Settings'}
              </button>
            </div>
          </div>
        </form>
      </section>
    </div>
  )
}

function VoiceModal({ onClose, onDataChanged }: { onClose: () => void; onDataChanged: () => void }) {
  const { t, language, setLanguage } = useLanguage()
  const [isListening, setIsListening] = useState(false)
  const [listeningStatus, setListeningStatus] = useState('')
  const [inputQuery, setInputQuery] = useState('')
  const [loading, setLoading] = useState(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  const getGreeting = (lang: string) => {
    if (lang.startsWith('te')) {
      return 'నమస్కారం! నేను మీ భారత్‌వాయిస్ అసిస్టెంట్‌ని. ఈ రోజు అమ్మకాలు, లాభం, స్టాక్ లేదా కొత్త బిల్లు గురించి అడగండి.'
    }
    if (lang.startsWith('hi')) {
      return 'नमस्ते! मैं आपका भारतवॉइस सहायक हूँ। आज की बिक्री, मुनाफ़ा, स्टॉक या नए बिल के बारे में पूछें।'
    }
    if (lang.startsWith('ta')) {
      return 'வணக்கம்! நான் உங்கள் பாரத்வாய்ஸ் உதவியாளர். இன்றைய விற்பனை, லாபம் அல்லது இருப்பு பற்றி கேட்கலாம்.'
    }
    if (lang.startsWith('kn')) {
      return 'ನಮಸ್ಕಾರ! ನಾನು ನಿಮ್ಮ ಭಾರತ್‌ವಾಯ್ಸ್ ಸಹಾಯಕ. ಇಂದಿನ ಮಾರಾಟ, ಲಾಭ ಅಥವಾ ಸ್ಟಾಕ್ ಬಗ್ಗೆ ಕೇಳಬಹುದು.'
    }
    if (lang.startsWith('ml')) {
      return 'നമസ്കാരം! ഞാൻ നിങ്ങളുടെ ഭാരത് വോയ്സ് അസിസ്റ്റന്റ് ആണ്. ഇന്നത്തെ വിൽപ്പന, ലാഭം അല്ലെങ്കിൽ സ്റ്റോക്ക് വിവരങ്ങൾ ചോദിക്കാം.'
    }
    return "Hello! I am your BharatVoice Assistant. You can ask about today's sales, profit, stock, cash balance, or create a bill."
  }

  const [messages, setMessages] = useState<Array<{ sender: 'user' | 'assistant'; text: string; data?: any }>>([
    { sender: 'assistant', text: getGreeting(language) }
  ])
  const [pendingAction, setPendingAction] = useState<{ query: string; action_payload: any; action_type?: string } | null>(null)
  const [executingAction, setExecutingAction] = useState(false)

  // Update greeting when language changes if only welcome message exists
  useEffect(() => {
    setMessages(prev => {
      if (prev.length <= 1) {
        return [{ sender: 'assistant', text: getGreeting(language) }]
      }
      return prev
    })
  }, [language])

  function stopMediaRecording() {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop()
      } catch (err) {
        console.warn('Error stopping media recorder:', err)
      }
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop())
      mediaStreamRef.current = null
    }
    setIsListening(false)
  }

  // Stop active recording on unmount
  useEffect(() => {
    return () => {
      stopMediaRecording()
    }
  }, [])

  async function handleSend(queryText: string) {
    const q = queryText.trim()
    if (!q) return
    setInputQuery('')
    setListeningStatus('')
    setMessages(prev => [...prev, { sender: 'user', text: q }])
    setLoading(true)

    try {
      const res = await api.voiceProcess(q, false, null, language)
      if (res.audio_base64) {
        try {
          const audio = new Audio('data:audio/wav;base64,' + res.audio_base64)
          audio.playbackRate = 1.0
          audio.preservesPitch = true
          await audio.play()
        } catch (audioErr) {
          console.warn('Gnani Timbre audio playback notice:', audioErr)
        }
      }

      if (res.action_required && res.action_payload) {
        setPendingAction({ query: q, action_payload: res.action_payload, action_type: res.action_type })
      } else {
        setPendingAction(null)
      }

      setMessages(prev => [...prev, { sender: 'assistant', text: res.reply, data: res.data }])
    } catch (err: any) {
      const errMsg = err?.message || 'Could not process voice query via Gnani AI.'
      setMessages(prev => [...prev, { sender: 'assistant', text: errMsg }])
    } finally {
      setLoading(false)
    }
  }

  async function startListeningSession() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setListeningStatus('Microphone recording is not supported in this browser.')
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      mediaStreamRef.current = stream
      const mr = new MediaRecorder(stream)
      mediaRecorderRef.current = mr
      audioChunksRef.current = []

      mr.ondataavailable = e => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data)
        }
      }

      mr.onstop = async () => {
        setIsListening(false)
        if (mediaStreamRef.current) {
          mediaStreamRef.current.getTracks().forEach(t => t.stop())
          mediaStreamRef.current = null
        }

        if (audioChunksRef.current.length > 0) {
          const blob = new Blob(audioChunksRef.current, { type: 'audio/wav' })
          if (blob.size > 100) {
            setListeningStatus('Transcribing voice via Gnani Prisma STT...')
            try {
              const res = await api.voiceTranscribe(blob, language)
              const transcript = (res.transcript || res.transcription || '').trim()
              if (transcript) {
                setInputQuery(transcript)
                setListeningStatus('')
                void handleSend(transcript)
              } else {
                setListeningStatus(res.message || 'No speech detected by Gnani Prisma STT.')
              }
            } catch (err: any) {
              setListeningStatus(err?.message || 'Gnani STT transcription service unreachable.')
            }
          } else {
            setListeningStatus('Voice audio recording was too brief.')
          }
        }
      }

      mr.start(250)
      setIsListening(true)
      const curLang = LANGUAGES.find(l => l.code === language)?.label || 'selected language'
      setListeningStatus(`Listening via Gnani Prisma (${curLang})… speak, then click mic to send`)
    } catch (err: any) {
      setIsListening(false)
      setListeningStatus('Could not access microphone. Please allow microphone permissions.')
    }
  }

  function toggleListening() {
    if (isListening) {
      stopMediaRecording()
    } else {
      void startListeningSession()
    }
  }

  async function handleConfirmAction() {
    if (!pendingAction) return
    setExecutingAction(true)
    try {
      const res = await api.voiceProcess(pendingAction.query, true, pendingAction.action_payload, language)
      if (res.audio_base64) {
        try {
          const audio = new Audio('data:audio/wav;base64,' + res.audio_base64)
          audio.playbackRate = 1.0
          audio.preservesPitch = true
          await audio.play()
        } catch (audioErr) {
          console.warn('Gnani Timbre audio playback notice:', audioErr)
        }
      }
      setMessages(prev => [...prev, { sender: 'assistant', text: res.reply, data: res.data }])
      setPendingAction(null)
      onDataChanged()
    } catch (err: any) {
      setMessages(prev => [...prev, { sender: 'assistant', text: err?.message || 'Failed to execute confirmed action.' }])
    } finally {
      setExecutingAction(false)
    }
  }

  function handleCancelAction() {
    setPendingAction(null)
    const cancelMsg = language.startsWith('te')
      ? 'ఆదేశం రద్దు చేయబడింది.'
      : language.startsWith('hi')
      ? 'कार्रवाई रद्द कर दी गई।'
      : 'Action cancelled.'
    setMessages(prev => [...prev, { sender: 'assistant', text: cancelMsg }])
  }

  async function replayAssistantVoice(text: string) {
    try {
      const res = await api.voiceTTS(text, language)
      if (res.audio_base64) {
        const audio = new Audio('data:audio/wav;base64,' + res.audio_base64)
        audio.playbackRate = 1.0
        audio.preservesPitch = true
        await audio.play()
      }
    } catch (err) {
      console.warn('Gnani Timbre voice replay notice:', err)
    }
  }

  // Suggestions by language
  const chips = useMemo(() => {
    if (language.startsWith('te')) {
      return [
        'ఈ రోజు అమ్మకాలు ఎంత?',
        'ఈ రోజు లాభం ఎంత?',
        'టాటా సాల్ట్ స్టాక్ ఎంత?',
        'క్యాష్ ఎంత ఉంది?',
        'తక్కువ స్టాక్ ఉన్నవి ఏవి?',
        'Add 10 packets of Sugar price 45',
        'Record 500 electricity expense'
      ]
    }
    if (language.startsWith('hi')) {
      return [
        'आज की बिक्री कितनी है?',
        'आज का मुनाफा कितना है?',
        'टाटा साल्ट का स्टॉक कितना है?',
        'कैश कितना है?',
        'कम स्टॉक वाले आइटम',
        'Add 10 packets of Sugar price 45',
        'Record 500 electricity expense'
      ]
    }
    return [
      "Show today's sales",
      'What is my profit today?',
      'Stock of Tata Salt',
      'How much cash in drawer?',
      'Which items are low in stock?',
      'Add 10 packets of Sugar price 45',
      'Record 500 electricity expense'
    ]
  }, [language])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <section className="voice-modal" style={{ maxWidth: 540 }} role="dialog" aria-modal="true" aria-labelledby="voice-title">
        <button className="icon-button modal-close" onClick={onClose} aria-label="Close dialog"><X size={18}/></button>
        <div className={`modal-mic ${isListening ? 'mic-btn-active' : ''}`} onClick={toggleListening} style={{ cursor: 'pointer' }}>
          <Mic size={26}/>
        </div>
        <div className="eyebrow">BHARATVOICE ASSISTANT</div>
        <h2 id="voice-title">{t('Your store, your language.')}</h2>

        {/* Quick Language Switcher Bar */}
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'center', margin: '4px 0 8px' }}>
          {LANGUAGES.map(l => (
            <button
              key={l.code}
              type="button"
              style={{
                fontSize: 11,
                padding: '3px 8px',
                borderRadius: 14,
                border: '1px solid ' + (language === l.code ? '#1b4332' : '#d8ded8'),
                background: language === l.code ? '#1b4332' : '#f8faf8',
                color: language === l.code ? '#fff' : '#415245',
                cursor: 'pointer',
                fontWeight: language === l.code ? 600 : 400
              }}
              onClick={() => {
                setLanguage(l.code)
                if (isListening) {
                  toggleListening()
                }
              }}
            >
              {l.native} · {l.label}
            </button>
          ))}
        </div>

        {/* Listening status prompt */}
        <div style={{ fontSize: 11, color: isListening ? '#1b4332' : '#68776b', marginTop: 2, fontWeight: isListening ? 600 : 400, minHeight: 18, textAlign: 'center' }}>
          {listeningStatus || (isListening ? 'Listening… speak now into your microphone' : 'Tap the microphone to speak, or type commands below')}
        </div>

        {/* Conversation box */}
        <div className="voice-chat-box">
          {messages.map((m, i) => (
            <div key={i} className={`voice-bubble ${m.sender}`}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 6 }}>
                <span>{m.text}</span>
                {m.sender === 'assistant' && (
                  <button
                    className="icon-button"
                    style={{ padding: 2, color: 'inherit', opacity: 0.7 }}
                    onClick={() => replayAssistantVoice(m.text)}
                    title="Play voice again (Gnani Timbre)"
                  >
                    <Volume2 size={13}/>
                  </button>
                )}
              </div>
            </div>
          ))}
          {loading && <div className="voice-bubble assistant" style={{ fontStyle: 'italic', color: '#777' }}>Thinking…</div>}
        </div>

        {/* Confirmation Card */}
        {pendingAction && (
          <div className="confirm-card">
            <strong>{t('Action Confirmation Required')}</strong>
            <p>Please review and confirm to apply this change to your store database:</p>
            <div style={{ fontSize: 11, background: '#fff', padding: '6px 10px', borderRadius: 7, marginBottom: 10 }}>
              {JSON.stringify(pendingAction.action_payload, null, 2)}
            </div>
            <div className="confirm-actions">
              <button className="button button-secondary button-small" onClick={handleCancelAction} disabled={executingAction}>
                {t('Cancel')}
              </button>
              <button className="button button-primary button-small" onClick={handleConfirmAction} disabled={executingAction}>
                {executingAction ? 'Confirming…' : t('Confirm')}
              </button>
            </div>
          </div>
        )}

        {/* Quick Suggestion Chips */}
        <div className="voice-chips">
          {chips.map((chip, idx) => (
            <button key={idx} className="voice-chip" onClick={() => void handleSend(chip)}>
              {chip}
            </button>
          ))}
        </div>

        {/* Input Row */}
        <form
          className="voice-input-row"
          onSubmit={e => {
            e.preventDefault()
            void handleSend(inputQuery)
          }}
        >
          <input
            className="voice-input-field"
            placeholder={isListening ? 'Listening to speech…' : 'Type a query or command in your language…'}
            value={inputQuery}
            onChange={e => setInputQuery(e.target.value)}
          />
          <button
            type="button"
            className={`button ${isListening ? 'button-primary mic-btn-active' : 'button-secondary'}`}
            style={{ width: 42, padding: 0, justifyContent: 'center' }}
            onClick={toggleListening}
            title={isListening ? 'Stop listening' : 'Start speaking'}
          >
            <Mic size={17}/>
          </button>
          <button
            type="submit"
            className="button button-primary"
            style={{ width: 42, padding: 0, justifyContent: 'center' }}
            disabled={!inputQuery.trim() || loading}
          >
            <Send size={15}/>
          </button>
        </form>
      </section>
    </div>
  )
}

export default App
