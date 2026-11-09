"""
Voice command processor and intent engine for BharatVoice AI.
Translates spoken or typed retail queries into actionable database operations with financial safety guards.
"""
import re
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, timedelta
from .models import (
    Product,
    Sale,
    SaleItem,
    StockMovement,
    BillImport,
    Expense,
    BusinessAccount,
    Supplier,
    PurchaseOrder,
    StoreProfile,
)
from .schemas import StockAdjustRequest
from .finance_service import calculate_financial_metrics, calculate_financial_analytics, calculate_inventory_valuation
from .accounts_service import record_account_transaction



def format_voice_result(result: Dict[str, Any], language_code: Optional[str]) -> Dict[str, Any]:
    """Translates intent reply into requested Indian language while preserving English reply."""
    if not result:
        return result
    lang = (language_code or "en-IN").strip().lower()
    english_reply = result.get("reply", "")
    result["reply_en"] = english_reply
    if lang.startswith("en"):
        return result

    intent = result.get("intent", "UNKNOWN")
    data = result.get("data")
    action_req = result.get("action_required", False)
    payload = result.get("action_payload") or {}

    # TELUGU (te-IN)
    if lang.startswith("te"):
        if action_req:
            if intent == "CREATE_PRODUCT":
                result["reply"] = f"దయచేసి నిర్ధారించండి: మీరు '{payload.get('name')}' ఉత్పత్తిని ₹{payload.get('price', 0):.2f} ధరకు మరియు {payload.get('stock_qty', 0)} ప్రారంభ నిల్వతో జోడించాలనుకుంటున్నారా?"
            elif intent == "CREATE_BILL":
                result["reply"] = f"దయచేసి నిర్ధారించండి: మీరు {payload.get('customer_name', 'వినియోగదారు')} కోసం మొత్తం ₹{payload.get('total', 0):.2f} బిల్లును సృష్టించాలనుకుంటున్నారా?"
            elif intent == "ADJUST_STOCK":
                result["reply"] = f"దయచేసి నిర్ధారించండి: మీరు స్టాక్ సర్దుబాటు చేయాలనుకుంటున్నారా?"
            elif intent == "CREATE_EXPENSE":
                result["reply"] = f"దయచేసి నిర్ధారించండి: మీరు {payload.get('category')} కోసం ₹{payload.get('amount', 0):.2f} ఖర్చును నమోదు చేయాలనుకుంటున్నారా?"
            elif intent == "RENAME_STORE":
                result["reply"] = f"దయచేసి నిర్ధారించండి: మీరు మీ దుకాణం పేరును '{payload.get('store_name')}'గా మార్చాలనుకుంటున్నారా?"
            return result

        if intent == "CREATE_PRODUCT" and data:
            result["reply"] = f"విజయవంతంగా '{data.get('name')}' ({data.get('sku')}) ఉత్పత్తిని ₹{data.get('price', 0):.2f} ధరకు మరియు ప్రారంభ నిల్వ {data.get('stock_qty', 0)} తో జోడించాము."
        elif intent == "RENAME_STORE" and data:
            result["reply"] = f"విజయవంతంగా మీ దుకాణం పేరును '{data.get('store_name')}'గా మార్చాము."
        elif intent == "CREATE_BILL" and data:
            result["reply"] = f"విజయవంతంగా {data.get('bill_number')} బిల్లును మొత్తం ₹{data.get('total', 0):.2f}కు సృష్టించాము."
        elif intent == "ADJUST_STOCK" and data:
            result["reply"] = f"స్టాక్ విజయవంతంగా సర్దుబాటు చేయబడింది. కొత్త నిల్వ: {data.get('new_stock')} యూనిట్లు."
        elif intent == "CREATE_EXPENSE" and data:
            result["reply"] = f"విజయవంతంగా ₹{data.get('amount', 0):.2f} ఖర్చును {data.get('category')} వర్గంలో నమోదు చేసాము."
        elif intent == "CHECK_PROFIT" and isinstance(data, dict):
            result["reply"] = (
                f"ఈ రోజు నికర అమ్మకాలు ₹{data.get('net_sales', 0)}. "
                f"వస్తువుల కొనుగోలు ధర ₹{data.get('cogs', 0)}, స్థూల లాభం ₹{data.get('gross_profit', 0)} ({data.get('gross_profit_margin', 0)}%). "
                f"నిర్వహణ ఖర్చులు ₹{data.get('operating_expenses', 0)} పోగా మీ నికర లాభం ₹{data.get('net_profit', 0)} ({data.get('net_profit_margin', 0)}%)."
            )
        elif intent == "CHECK_REVENUE" and isinstance(data, dict):
            result["reply"] = f"ఈ రోజు మొత్తం ₹{data.get('today_sales', 0):.2f} అమ్మకాలు {data.get('today_bills', 0)} పూర్తయిన బిల్లులలో నమోదయ్యాయి."
        elif intent == "CHECK_CASH_BALANCE" and isinstance(data, dict):
            result["reply"] = f"క్యాష్ కౌంటర్ డ్రాయర్‌లో ప్రస్తుతం ₹{data.get('current_balance', 0):.2f} నగదు అందుబాటులో ఉంది."
        elif intent == "CHECK_EXPENSES" and isinstance(data, dict):
            result["reply"] = f"ఈ నెల మొత్తం వ్యాపార నిర్వహణ ఖర్చులు ₹{data.get('operating_expenses', 0):.2f} నమోదయ్యాయి."
        elif intent == "TOP_PROFIT_PRODUCTS" and isinstance(data, list) and len(data) > 0:
            top = data[0]
            result["reply"] = f"మీ అత్యధిక లాభం కలిగిన ఉత్పత్తి {top.get('name')} ({top.get('sku')}), స్థూల లాభం ₹{top.get('gross_profit')} ({top.get('margin_percentage')}% మార్జిన్)."
        elif intent == "CHECK_TOTAL_STOCK" and isinstance(data, dict):
            result["reply"] = f"మీ వద్ద మొత్తం {data.get('total_items_count', 0)} ఉత్పత్తులలో {data.get('total_stock_units', 0)} యూనిట్ల నిల్వ ఉంది, కొనుగోలు విలువ ₹{data.get('total_cost_valuation', 0)} మరియు రిటైల్ అమ్మకపు విలువ సుమారు ₹{data.get('total_selling_valuation', 0)}."
        elif intent == "UNPAID_SUPPLIER_BILLS" and isinstance(data, dict):
            result["reply"] = f"మీకు {data.get('count', 0)} బకాయి సప్లయర్ ఇన్వాయిస్‌లు ఉన్నాయి, మొత్తం బకాయి మొత్తం ₹{data.get('total_outstanding', 0):.2f}."
        elif intent == "LOW_STOCK":
            items = data.get("products", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if items:
                items_str = ", ".join([f"{p['name']} ({p['stock_qty']} {p.get('unit', 'units')})" for p in items])
                result["reply"] = f"రీఆర్డర్ పరిమితి కంటే తక్కువగా {len(items)} ఉత్పత్తులు ఉన్నాయి: {items_str}."
            else:
                result["reply"] = "ప్రస్తుతం అన్ని ఉత్పత్తులు తగినంత నిల్వతో ఉన్నాయి."
        elif intent == "CHECK_STOCK" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ప్రస్తుతం {data.get('stock_qty')} {data.get('unit')} నిల్వ ఉంది, ధర ₹{data.get('price', 0):.2f}."
        elif intent == "CHECK_PRICE" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ధర ఒక {data.get('unit')}కు ₹{data.get('price', 0):.2f}."
        elif intent == "SEARCH_PRODUCT" and isinstance(data, list):
            if data:
                items_str = ", ".join([f"{p['name']} (₹{p['price']})" for p in data])
                result["reply"] = f"కనుగొనబడిన ఉత్పత్తులు: {items_str}."
            else:
                result["reply"] = "మీ శోధనకు తగిన ఉత్పత్తులు కనుగొనబడలేదు."
        elif intent == "RECENT_SALES" and isinstance(data, list):
            if data:
                sales_str = ", ".join([f"{s['bill_number']}: ₹{s['total']}" for s in data[:3]])
                result["reply"] = f"ఇటీవలి విక్రయ లావాదేవీలు: {sales_str}."
            else:
                result["reply"] = "ఇటీవలి విక్రయాలు ఏవీ లేవు."
        elif intent == "GREETING":
            result["reply"] = "నమస్కారం! నేను మీ భారత్‌వాయిస్ అసిస్టెంట్‌ని. ఈ రోజు అమ్మకాలు, లాభం, స్టాక్, క్యాష్ బ్యాలెన్స్ లేదా కొత్త బిల్లు గురించి అడగండి."
        elif intent == "UNKNOWN":
            result["reply"] = "క్షమించండి, మీ మాట అర్థం కాలేదు. మీరు ఈ రోజు లాభం, అమ్మకాలు, నిల్వ, క్యాష్ బ్యాలెన్స్ లేదా కొత్త బిల్లు గురించి అడగవచ్చు."
        return result

    # HINDI (hi-IN)
    if lang.startswith("hi"):
        if action_req:
            if intent == "CREATE_PRODUCT":
                result["reply"] = f"कृपया पुष्टि करें: क्या आप ₹{payload.get('price', 0):.2f} पर '{payload.get('name')}' को {payload.get('stock_qty', 0)} स्टॉक के साथ जोड़ना चाहते हैं?"
            elif intent == "CREATE_BILL":
                result["reply"] = f"कृपया पुष्टि करें: क्या आप {payload.get('customer_name', 'ग्राहक')} के लिए कुल ₹{payload.get('total', 0):.2f} का बिल बनाना चाहते हैं?"
            elif intent == "ADJUST_STOCK":
                result["reply"] = f"कृपया पुष्टि करें: क्या आप इस उत्पाद का स्टॉक समायोजित करना चाहते हैं?"
            elif intent == "CREATE_EXPENSE":
                result["reply"] = f"कृपया पुष्टि करें: क्या आप {payload.get('category')} के लिए ₹{payload.get('amount', 0):.2f} का खर्च दर्ज करना चाहते हैं?"
            elif intent == "RENAME_STORE":
                result["reply"] = f"कृपया पुष्टि करें: क्या आप अपनी दुकान का नाम बदलकर '{payload.get('store_name')}' करना चाहते हैं?"
            return result

        if intent == "CREATE_PRODUCT" and data:
            result["reply"] = f"सफलतापूर्वक '{data.get('name')}' ({data.get('sku')}) को ₹{data.get('price', 0):.2f} पर {data.get('stock_qty', 0)} स्टॉक के साथ जोड़ा गया।"
        elif intent == "RENAME_STORE" and data:
            result["reply"] = f"सफलतापूर्वक आपकी दुकान का नाम बदलकर '{data.get('store_name')}' कर दिया गया है।"
        elif intent == "CREATE_BILL" and data:
            result["reply"] = f"सफलतापूर्वक बिल {data.get('bill_number')} कुल ₹{data.get('total', 0):.2f} में बनाया गया।"
        elif intent == "ADJUST_STOCK" and data:
            result["reply"] = f"स्टॉक सफलतापूर्वक समायोजित किया गया। नया बैलेंस: {data.get('new_stock')} यूनिट।"
        elif intent == "CREATE_EXPENSE" and data:
            result["reply"] = f"सफलतापूर्वक ₹{data.get('amount', 0):.2f} का खर्च {data.get('category')} में दर्ज किया गया।"
        elif intent == "CHECK_PROFIT" and isinstance(data, dict):
            result["reply"] = (
                f"आज की कुल बिक्री ₹{data.get('net_sales', 0)} है। "
                f"माल की लागत ₹{data.get('cogs', 0)} है और सकल मुनाफा ₹{data.get('gross_profit', 0)} ({data.get('gross_profit_margin', 0)}%) है। "
                f"₹{data.get('operating_expenses', 0)} खर्च के बाद आपका शुद्ध मुनाफा ₹{data.get('net_profit', 0)} ({data.get('net_profit_margin', 0)}%) है।"
            )
        elif intent == "CHECK_REVENUE" and isinstance(data, dict):
            result["reply"] = f"आज की कुल बिक्री ₹{data.get('today_sales', 0):.2f} है, जिसमें {data.get('today_bills', 0)} पूरे बिल शामिल हैं।"
        elif intent == "CHECK_CASH_BALANCE" and isinstance(data, dict):
            result["reply"] = f"कैश काउंटर में अभी ₹{data.get('current_balance', 0):.2f} नकद उपलब्ध है।"
        elif intent == "CHECK_EXPENSES" and isinstance(data, dict):
            result["reply"] = f"इस महीने कुल परिचालन खर्च ₹{data.get('operating_expenses', 0):.2f} दर्ज किया गया है।"
        elif intent == "TOP_PROFIT_PRODUCTS" and isinstance(data, list) and len(data) > 0:
            top = data[0]
            result["reply"] = f"आपका सबसे अधिक मुनाफे वाला उत्पाद {top.get('name')} ({top.get('sku')}) है, जिसका सकल मुनाफा ₹{top.get('gross_profit')} ({top.get('margin_percentage')}% मार्जिन) है।"
        elif intent == "CHECK_TOTAL_STOCK" and isinstance(data, dict):
            result["reply"] = f"आपके पास {data.get('total_items_count', 0)} उत्पादों में कुल {data.get('total_stock_units', 0)} यूनिट्स स्टॉक है, जिसकी लागत ₹{data.get('total_cost_valuation', 0)} और खुदरा मूल्य लगभग ₹{data.get('total_selling_valuation', 0)} है।"
        elif intent == "UNPAID_SUPPLIER_BILLS" and isinstance(data, dict):
            result["reply"] = f"सप्लायर के {data.get('count', 0)} बकाया बिल हैं, कुल बकाया राशि ₹{data.get('total_outstanding', 0):.2f} है।"
        elif intent == "LOW_STOCK":
            items = data.get("products", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if items:
                items_str = ", ".join([f"{p['name']} ({p['stock_qty']} {p.get('unit', 'units')})" for p in items])
                result["reply"] = f"रीऑर्डर स्तर से नीचे {len(items)} उत्पाद हैं: {items_str}।"
            else:
                result["reply"] = "सभी उत्पादों का स्टॉक सामान्य है।"
        elif intent == "CHECK_STOCK" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} का वर्तमान स्टॉक {data.get('stock_qty')} {data.get('unit')} है, दर ₹{data.get('price', 0):.2f} प्रति यूनिट।"
        elif intent == "CHECK_PRICE" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} की कीमत प्रति {data.get('unit')} ₹{data.get('price', 0):.2f} है।"
        elif intent == "SEARCH_PRODUCT" and isinstance(data, list):
            if data:
                items_str = ", ".join([f"{p['name']} (₹{p['price']})" for p in data])
                result["reply"] = f"मिले हुए उत्पाद: {items_str}।"
            else:
                result["reply"] = "आपकी खोज के अनुसार कोई उत्पाद नहीं मिला।"
        elif intent == "RECENT_SALES" and isinstance(data, list):
            if data:
                sales_str = ", ".join([f"{s['bill_number']}: ₹{s['total']}" for s in data[:3]])
                result["reply"] = f"हाल की बिक्री: {sales_str}।"
            else:
                result["reply"] = "कोई हालिया बिक्री दर्ज नहीं है।"
        elif intent == "GREETING":
            result["reply"] = "नमस्ते! मैं आपका भारतवॉइस सहायक हूँ। आज की बिक्री, मुनाफ़ा, स्टॉक, कैश बैलेंस या नए बिल के बारे में पूछें।"
        elif intent == "UNKNOWN":
            result["reply"] = "माफ़ कीजिये, आपकी बात समझ नहीं आई। आप आज का मुनाफा, आज की बिक्री, स्टॉक, कैश बैलेंस या नया बिल बनाने के बारे में पूछ सकते हैं।"
        return result

    # TAMIL (ta-IN)
    if lang.startswith("ta"):
        if action_req:
            result["reply"] = f"தயவுசெய்து உறுதிப்படுத்தவும்: {english_reply}"
            return result
        if intent == "CHECK_PROFIT" and isinstance(data, dict):
            result["reply"] = f"இன்றைய நிகர விற்பனை ₹{data.get('net_sales', 0)}. பொருட்கள் அடக்க விலை ₹{data.get('cogs', 0)}, மொத்த லாபம் ₹{data.get('gross_profit', 0)}. நிகர லாபம் ₹{data.get('net_profit', 0)}."
        elif intent == "CHECK_REVENUE" and isinstance(data, dict):
            result["reply"] = f"இன்று {data.get('today_bills', 0)} பில்களில் மொத்த விற்பனை ₹{data.get('today_sales', 0):.2f}."
        elif intent == "CHECK_CASH_BALANCE" and isinstance(data, dict):
            result["reply"] = f"பணப் பெட்டியில் தற்போது ₹{data.get('current_balance', 0):.2f} உள்ளது."
        elif intent == "CHECK_EXPENSES" and isinstance(data, dict):
            result["reply"] = f"இந்த மாத மொத்த செலவு ₹{data.get('operating_expenses', 0):.2f}."
        elif intent == "CHECK_STOCK" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} தற்போது {data.get('stock_qty')} {data.get('unit')} இருப்பில் உள்ளது, விலை ₹{data.get('price', 0):.2f}."
        elif intent == "CHECK_PRICE" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ஒரு {data.get('unit')} விலை ₹{data.get('price', 0):.2f}."
        elif intent == "LOW_STOCK" and isinstance(data, list):
            result["reply"] = f"குறைந்த இருப்பில் {len(data)} பொருட்கள் உள்ளன." if data else "அனைத்து பொருட்களும் போதிய இருப்பில் உள்ளன."
        elif intent == "GREETING":
            result["reply"] = "வணக்கம்! நான் உங்கள் பாரத்வாய்ஸ் உதவியாளர். இன்றைய விற்பனை, லாபம் அல்லது இருப்பு பற்றி கேட்கலாம்."
        elif intent == "UNKNOWN":
            result["reply"] = "மன்னிக்கவும், உங்கள் கட்டளை புரியவில்லை. இன்றைய விற்பனை, லாபம் அல்லது இருப்பு பற்றி கேட்கலாம்."
        return result

    # KANNADA (kn-IN)
    if lang.startswith("kn"):
        if action_req:
            result["reply"] = f"ದಯವಿಟ್ಟು ದೃಢೀಕರಿಸಿ: {english_reply}"
            return result
        if intent == "CHECK_PROFIT" and isinstance(data, dict):
            result["reply"] = f"ಇವತ್ತಿನ ಒಟ್ಟು ಮಾರಾಟ ₹{data.get('net_sales', 0)}. ಒಟ್ಟು ಲಾಭ ₹{data.get('gross_profit', 0)}. ನಿಮ್ಮ ನಿವ್ವಳ ಲಾಭ ₹{data.get('net_profit', 0)}."
        elif intent == "CHECK_REVENUE" and isinstance(data, dict):
            result["reply"] = f"ಇಂದು {data.get('today_bills', 0)} ಬಿಲ್‌ಗಳಲ್ಲಿ ಒಟ್ಟು ₹{data.get('today_sales', 0):.2f} ಮಾರಾಟವಾಗಿದೆ."
        elif intent == "CHECK_CASH_BALANCE" and isinstance(data, dict):
            result["reply"] = f"ಕ್ಯಾಶ್ ಕೌಂಟರ್‌ನಲ್ಲಿ ಪ್ರಸ್ತುತ ₹{data.get('current_balance', 0):.2f} ನಗದು ಇದೆ."
        elif intent == "CHECK_EXPENSES" and isinstance(data, dict):
            result["reply"] = f"ಈ ತಿಂಗಳ ಒಟ್ಟು ಖರ್ಚು ₹{data.get('operating_expenses', 0):.2f}."
        elif intent == "CHECK_STOCK" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ಪ್ರಸ್ತುತ {data.get('stock_qty')} {data.get('unit')} ದಾಸ್ತಾನು ಇದೆ, ದರ ₹{data.get('price', 0):.2f}."
        elif intent == "CHECK_PRICE" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ಬೆಲೆ ಒಂದು {data.get('unit')}ಗೆ ₹{data.get('price', 0):.2f}."
        elif intent == "LOW_STOCK" and isinstance(data, list):
            result["reply"] = f"ಕಡಿಮೆ ದಾಸ್ತಾನಿನಲ್ಲಿ {len(data)} ವಸ್ತುಗಳಿವೆ." if data else "ಎಲ್ಲಾ ವಸ್ತುಗಳು ಸಾಕಷ್ಟು ದಾಸ್ತಾನಿನಲ್ಲಿವೆ."
        elif intent == "GREETING":
            result["reply"] = "ನಮಸ್ಕಾರ! ನಾನು ನಿಮ್ಮ ಭಾರತ್‌ವಾಯ್ಸ್ ಸಹಾಯಕ. ಇಂದಿನ ಮಾರಾಟ, ಲಾಭ ಅಥವಾ ದಾಸ್ತಾನಿನ ಬಗ್ಗೆ ಕೇಳಬಹುದು."
        elif intent == "UNKNOWN":
            result["reply"] = "ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಆಜ್ಞೆ ಅರ್ಥವಾಗಲಿಲ್ಲ. ಇಂದಿನ ಮಾರಾಟ, ಲಾಭ ಅಥವಾ ದಾಸ್ತಾನಿನ ಬಗ್ಗೆ ಕೇಳಬಹುದು."
        return result

    # MALAYALAM (ml-IN)
    if lang.startswith("ml"):
        if action_req:
            result["reply"] = f"ദയവായി സ്ഥിരീകരിക്കുക: {english_reply}"
            return result
        if intent == "CHECK_PROFIT" and isinstance(data, dict):
            result["reply"] = f"ഇന്നത്തെ ആകെ വിൽപ്പന ₹{data.get('net_sales', 0)}. മൊത്തം ലാഭം ₹{data.get('gross_profit', 0)}. അറ്റാദായം ₹{data.get('net_profit', 0)}."
        elif intent == "CHECK_REVENUE" and isinstance(data, dict):
            result["reply"] = f"ഇന്ന് {data.get('today_bills', 0)} ബില്ലുകളിലായി ആകെ ₹{data.get('today_sales', 0):.2f} വിൽപ്പന നടന്നു."
        elif intent == "CHECK_CASH_BALANCE" and isinstance(data, dict):
            result["reply"] = f"ക്യാഷ് കൗണ്ടറിൽ ഇപ്പോൾ ₹{data.get('current_balance', 0):.2f} ഉണ്ട്."
        elif intent == "CHECK_EXPENSES" and isinstance(data, dict):
            result["reply"] = f"ഈ മാസത്തെ ആകെ ചെലവ് ₹{data.get('operating_expenses', 0):.2f}."
        elif intent == "CHECK_STOCK" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ഇപ്പോൾ {data.get('stock_qty')} {data.get('unit')} സ്റ്റോക്കുണ്ട്, വില ₹{data.get('price', 0):.2f}."
        elif intent == "CHECK_PRICE" and isinstance(data, dict):
            result["reply"] = f"{data.get('name')} ഒരു {data.get('unit')} വില ₹{data.get('price', 0):.2f}."
        elif intent == "LOW_STOCK" and isinstance(data, list):
            result["reply"] = f"കുറഞ്ഞ സ്റ്റോക്കിൽ {len(data)} ഇനങ്ങളുണ്ട്." if data else "എല്ലാ ഇനങ്ങളും ആവശ്യത്തിന് സ്റ്റോക്കുണ്ട്."
        elif intent == "GREETING":
            result["reply"] = "നമസ്കാരം! ഞാൻ നിങ്ങളുടെ ഭാരത് വോയ്സ് അസിസ്റ്റന്റ് ആണ്. ഇന്നത്തെ വിൽപ്പന, ലാഭം അല്ലെങ്കിൽ സ്റ്റോക്ക് വിവരങ്ങൾ ചോദിക്കാം."
        elif intent == "UNKNOWN":
            result["reply"] = "ക്ഷമിക്കണം, നിർദ്ദേശം മനസ്സിലായില്ല. ഇന്നത്തെ വിൽപ്പന, ലാഭം അല്ലെങ്കിൽ സ്റ്റോക്ക് വിവരങ്ങൾ ചോദിക്കാം."
        return result

    return result


def _internal_process_voice_intent(
    query: str,
    db: Session,
    store_id: str = "DEFAULT",
    confirmed: bool = False,
    action_payload: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Parses natural language query into intent and executes safe queries or stages confirmation actions.
    Supported Intents:
    - CHECK_PROFIT ("how much profit did I make today", "today's profit", "aaj ka munafa")
    - CHECK_REVENUE ("today's sales", "how much revenue today", "aaj ka sales")
    - CHECK_CASH_BALANCE ("how much money is in my cash account", "cash drawer balance")
    - CHECK_EXPENSES ("show my expenses this month", "expenses this month")
    - TOP_PROFIT_PRODUCTS ("which product gives me the highest profit", "highest profit")
    - CHECK_TOTAL_STOCK ("how much stock do I have", "inventory valuation")
    - UNPAID_SUPPLIER_BILLS ("show unpaid supplier bills", "supplier dues")
    - CREATE_EXPENSE ("create an expense of 500 rupees for electricity") -> Requires confirmation
    - CREATE_PRODUCT ("add 10 packets of Aashirvaad Aata", "create product Tata Tea price 150") -> Requires confirmation
    - CREATE_BILL ("create bill for 2 Tata Salt for customer Suresh") -> Requires confirmation
    - CHECK_STOCK ("stock of Tata Salt", "how much Atta left")
    - CHECK_PRICE ("price of Amul Butter", "kitna price hai salt ka")
    - SEARCH_PRODUCT ("find Maggi", "search rice", "dhoondo chawal")
    - LOW_STOCK ("which items are low in stock", "reorder list")
    - RECENT_SALES ("show recent bills", "last transactions")
    - ADJUST_STOCK ("add 10 stock to Tata Salt", "deduct 2 units") -> Requires confirmation
    - EXPLAIN_IMPORT_ERRORS ("why did bill fail", "explain imported errors")
    """
    q = query.strip().lower()
    if not q:
        return {
            "intent": "UNKNOWN",
            "reply": "Please say a command or ask a question about your store.",
            "action_required": False,
        }

    # Confirmed action execution from staged payload
    if confirmed and action_payload:
        intent = action_payload.get("intent") or "CREATE_EXPENSE"

        if intent == "CREATE_PRODUCT":
            name = action_payload.get("name", "New Product")
            price = float(action_payload.get("price", 100.0))
            cost_price = float(action_payload.get("cost_price", round(price * 0.8, 2)))
            stock_qty = float(action_payload.get("stock_qty", 0.0))
            unit = action_payload.get("unit", "pack")
            category = action_payload.get("category", "General")

            count = db.query(Product).count() + 1
            sku = f"BV-{1000 + count}"
            while db.query(Product).filter(Product.sku == sku).first():
                count += 1
                sku = f"BV-{1000 + count}"

            prod = Product(
                sku=sku,
                name=name,
                category=category,
                unit=unit,
                price=price,
                cost_price=cost_price,
                stock_qty=stock_qty,
                reorder_level=5.0,
                is_active=1,
                store_id=store_id,
                created_at=datetime.utcnow(),
            )
            db.add(prod)
            db.flush()
            if stock_qty > 0:
                mov = StockMovement(
                    product_id=prod.id,
                    quantity_change=stock_qty,
                    balance_after=stock_qty,
                    reason="VOICE_INITIAL_STOCK",
                    notes=f"Initial stock via voice product creation: {name}",
                    created_at=datetime.utcnow(),
                )
                db.add(mov)
            db.commit()
            return {
                "intent": "CREATE_PRODUCT",
                "reply": f"Successfully created product '{name}' ({sku}) at ₹{price:.2f} with initial stock of {stock_qty} {unit}.",
                "data": {"product_id": prod.id, "sku": prod.sku, "name": prod.name, "price": prod.price, "stock_qty": prod.stock_qty},
                "action_required": False,
            }

        elif intent == "CREATE_BILL":
            items = action_payload.get("items", [])
            customer_name = action_payload.get("customer_name", "Walk-in Customer")
            payment_method = action_payload.get("payment_method", "Cash")

            timestamp_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            bill_no = f"BV-SALE-{timestamp_str}"

            total_amount = 0.0
            line_creations = []
            for it in items:
                p = db.query(Product).filter(Product.id == it["product_id"]).first()
                if not p:
                    continue
                qty = float(it["quantity"])
                if p.stock_qty < qty:
                    return {
                        "intent": "CREATE_BILL",
                        "reply": f"Cannot complete bill: Insufficient stock for {p.name}. Required: {qty}, Available: {p.stock_qty}.",
                        "action_required": False,
                    }
                ltot = round(qty * p.price, 2)
                total_amount += ltot
                line_creations.append((p, qty, ltot))

            if not line_creations:
                return {
                    "intent": "CREATE_BILL",
                    "reply": "No valid products were found to create the bill.",
                    "action_required": False,
                }

            cash_acc = (
                db.query(BusinessAccount)
                .filter(BusinessAccount.store_id == store_id, BusinessAccount.account_type == "CASH")
                .first()
            )

            sale = Sale(
                bill_number=bill_no,
                customer_name=customer_name,
                payment_method=payment_method,
                business_account_id=cash_acc.id if cash_acc else None,
                status="Completed",
                total=round(total_amount, 2),
                discount=0.0,
                store_id=store_id,
                created_at=datetime.utcnow(),
            )
            db.add(sale)
            db.flush()

            for prod, qty, ltot in line_creations:
                new_stock = round(prod.stock_qty - qty, 4)
                prod.stock_qty = new_stock
                s_item = SaleItem(
                    sale_id=sale.id,
                    product_id=prod.id,
                    product_name=prod.name,
                    quantity=qty,
                    unit_price=prod.price,
                    unit_cost=prod.cost_price,
                    line_total=ltot,
                )
                db.add(s_item)
                mov = StockMovement(
                    product_id=prod.id,
                    quantity_change=-qty,
                    balance_after=new_stock,
                    reason="DIRECT_SALE",
                    sale_id=sale.id,
                    notes=f"Sold via voice bill {bill_no}",
                    created_at=datetime.utcnow(),
                )
                db.add(mov)

            if cash_acc:
                record_account_transaction(
                    db=db,
                    account_id=cash_acc.id,
                    transaction_type="INCOME",
                    amount=round(total_amount, 2),
                    reference_type="SALE",
                    reference_id=str(sale.id),
                    description=f"Sales receipt {bill_no} via voice",
                    store_id=store_id,
                )

            db.commit()
            return {
                "intent": "CREATE_BILL",
                "reply": f"Successfully created bill {bill_no} for {customer_name} totaling ₹{total_amount:.2f}.",
                "data": {"sale_id": sale.id, "bill_number": bill_no, "total": total_amount},
                "action_required": False,
            }

        elif intent == "ADJUST_STOCK":
            prod_id = action_payload.get("product_id")
            change = float(action_payload.get("quantity_change", 0.0))
            direction = action_payload.get("direction", "adjust")
            prod = db.query(Product).filter(Product.id == prod_id).first()
            if prod:
                new_bal = round(prod.stock_qty + change, 4)
                if new_bal < 0:
                    return {
                        "intent": "ADJUST_STOCK",
                        "reply": f"Cannot deduct: stock would become negative ({new_bal}).",
                        "action_required": False,
                    }
                prod.stock_qty = new_bal
                mov = StockMovement(
                    product_id=prod.id,
                    quantity_change=change,
                    balance_after=new_bal,
                    reason="VOICE_ADJUSTMENT",
                    notes=f"Stock {direction}ed via voice confirmation",
                    created_at=datetime.utcnow(),
                )
                db.add(mov)
                db.commit()
                return {
                    "intent": "ADJUST_STOCK",
                    "reply": f"Successfully {direction}ed {abs(change)} {prod.unit} for {prod.name}. New balance: {new_bal} {prod.unit}.",
                    "data": {"product_id": prod.id, "new_stock": new_bal},
                    "action_required": False,
                }

        elif intent == "CREATE_EXPENSE" or "amount" in action_payload:
            amount = float(action_payload.get("amount", 0.0))
            category = action_payload.get("category", "General")
            now = datetime.utcnow()
            cash_acc = (
                db.query(BusinessAccount)
                .filter(BusinessAccount.store_id == store_id, BusinessAccount.account_type == "CASH")
                .first()
            )
            exp = Expense(
                store_id=store_id,
                category=category,
                amount=amount,
                expense_date=now,
                payment_method="Cash",
                description=action_payload.get("description", f"{category} expense via voice"),
                business_account_id=cash_acc.id if cash_acc else None,
                status="ACTIVE",
                created_at=now,
            )
            db.add(exp)
            db.flush()

            if cash_acc:
                record_account_transaction(
                    db=db,
                    account_id=cash_acc.id,
                    transaction_type="EXPENSE",
                    amount=amount,
                    reference_type="EXPENSE",
                    reference_id=str(exp.id),
                    description=f"{category} expense",
                    store_id=store_id,
                )

            db.commit()
            return {
                "intent": "CREATE_EXPENSE",
                "reply": f"Successfully recorded an expense of ₹{amount:.2f} for {category}.",
                "data": {"expense_id": exp.id, "amount": amount, "category": category},
                "action_required": False,
            }

        elif intent == "RENAME_STORE":
            new_name = action_payload.get("store_name", "My Retail Store").strip()
            sp = db.query(StoreProfile).filter(StoreProfile.store_id == store_id).first()
            if not sp:
                sp = StoreProfile(store_id=store_id, store_name=new_name, updated_at=datetime.utcnow())
                db.add(sp)
            else:
                sp.store_name = new_name
                sp.updated_at = datetime.utcnow()
            db.commit()
            return {
                "intent": "RENAME_STORE",
                "reply": f"Successfully renamed your store to '{new_name}'.",
                "data": {"store_id": store_id, "store_name": new_name},
                "action_required": False,
            }

    # 0. Greetings
    if any(q == g or q.startswith(g + " ") for g in [
        "hi", "hello", "hey", "namaste", "namaskar", "namaskaram", "vanakkam", "namaskara",
        "నమస్కారం", "నమస్తే", "హలో",
        "नमस्ते", "नमस्कार", "हेलो", "प्रणाम",
        "வணக்கம்", "ஹலோ",
        "ನಮಸ್ಕಾರ", "ಹಲೋ",
        "നമസ്കാരം", "ഹലോ"
    ]):
        if len(q.split()) <= 4:
            return {
                "intent": "GREETING",
                "reply": "Hello! I am your BharatVoice assistant. You can ask me about today's sales, profit, stock, cash balance, or create bills and products.",
                "data": None,
                "action_required": False,
            }

    # 1. Profit intelligence ("how much profit did I make today", "today's profit", "aaj ka munafa", "profit")
    if any(k in q for k in [
        "how much profit", "todays profit", "today's profit", "profit today", "today profit", "net profit today",
        "profit", "munafa", "labh", "laabham", "aadaayam", "laabam", "laabha",
        "मुनाफा", "मुनाफ़ा", "लाभ", "లాభం", "ఆదாயம்", "லாபம்", "ಲಾಭ", "ಲಾഭം"
    ]):
        metrics = calculate_financial_metrics(db, store_id=store_id, period="today")
        reply = (
            f"Today's net sales are ₹{metrics['net_sales']}. "
            f"Cost of goods sold is ₹{metrics['cogs']}, leaving a gross profit of ₹{metrics['gross_profit']} ({metrics['gross_profit_margin']}%). "
            f"After ₹{metrics['operating_expenses']} in operating expenses, your net profit is ₹{metrics['net_profit']} ({metrics['net_profit_margin']}%)."
        )
        return {
            "intent": "CHECK_PROFIT",
            "reply": reply,
            "data": metrics,
            "action_required": False,
        }

    # 2. Today's Revenue / Sales summary
    if any(k in q for k in [
        "today's sales", "todays sales", "revenue today", "today sales", "sales today", "how much sales",
        "aaj ka sale", "aaj ki kamai", "today revenue", "aaj ki bikri", "bikri", "ammakaalu", "virpanai",
        "sale", "sales", "revenue", "maarata", "vilppana", "बिक्री", "कमाई", "అమ్మకాలు", "விற்பனை", "ಮಾರಾಟ", "വിൽപ്പന"
    ]):
        today = date.today()
        start_today = datetime.combine(today, datetime.min.time())
        start_tomorrow = start_today + timedelta(days=1)
        today_sales = (
            db.query(func.coalesce(func.sum(Sale.total), 0.0))
            .filter(
                Sale.store_id == store_id,
                Sale.created_at >= start_today,
                Sale.created_at < start_tomorrow,
                Sale.status == "Completed",
            )
            .scalar()
            or 0.0
        )
        today_bills = (
            db.query(Sale)
            .filter(
                Sale.store_id == store_id,
                Sale.created_at >= start_today,
                Sale.created_at < start_tomorrow,
                Sale.status == "Completed",
            )
            .count()
        )
        return {
            "intent": "CHECK_REVENUE",
            "reply": f"Today's total sales are ₹{round(float(today_sales), 2)} across {today_bills} completed bills.",
            "data": {"today_sales": float(today_sales), "today_bills": today_bills},
            "action_required": False,
        }

    # 3. Cash account balance ("how much money is in my cash account", "cash drawer", "cash balance")
    if any(k in q for k in [
        "cash account", "money in my cash", "cash drawer", "cash balance", "cash counter",
        "cash", "drawer", "tijori", "kitna cash hai", "paisa kitna", "dabbulu entha", "panam evalavu", "hana eshtu", "panam ethra",
        "कैश", "रोकड़", "तिजोरी", "నగదు", "క్యాష్", "డబ్బులు", "பணம்", "ಹಣ", "ಕ್ಯಾಶ್", "പണം"
    ]) and not any(k in q for k in ["expense", "bill", "sale", "add product", "create product"]):
        cash_acc = (
            db.query(BusinessAccount)
            .filter(BusinessAccount.store_id == store_id, BusinessAccount.account_type == "CASH")
            .first()
        )
        bal = cash_acc.current_balance if cash_acc else 0.0
        return {
            "intent": "CHECK_CASH_BALANCE",
            "reply": f"The cash counter drawer currently holds ₹{bal:.2f}.",
            "data": {"account_id": cash_acc.id if cash_acc else None, "current_balance": bal},
            "action_required": False,
        }

    # 4. Expenses this month ("show my expenses this month", "expenses this month", "monthly expenses", "expenses")
    if any(k in q for k in [
        "expenses this month", "show my expenses", "show expenses", "monthly expenses",
        "expenses", "expense", "kharcha kitna", "kharch", "kharcha", "kharchulu", "selavugal", "chelave",
        "खर्चा", "खर्च", "ఖర్చులు", "ఖర్చు", "செலவு", "ಖರ್ಚು", "ചെലവ്"
    ]) and not any(k in q for k in ["create", "add", "record", "new", "enter"]):
        metrics = calculate_financial_metrics(db, store_id=store_id, period="this_month")
        total_exp = metrics["operating_expenses"]
        return {
            "intent": "CHECK_EXPENSES",
            "reply": f"Total operating expenses recorded this month are ₹{total_exp:.2f}.",
            "data": {"operating_expenses": total_exp, "period": "This Month"},
            "action_required": False,
        }

    # 5. Top profit products ("which product gives me the highest profit", "highest profit")
    if any(k in q for k in [
        "highest profit", "most profit", "highest margin", "best profit product",
        "sabse jyada munafa", "ekkuva laabham", "athiga laabam", "hecchina laabha", "kooduthal laabham"
    ]):
        analytics = calculate_financial_analytics(db, store_id=store_id, period="30d")
        highest = analytics.get("highest_profit_products", [])
        if highest:
            top = highest[0]
            reply = f"Your highest profit product is {top['name']} ({top['sku']}) with ₹{top['gross_profit']} gross profit ({top['margin_percentage']}% margin)."
        else:
            reply = "Not enough sales data recorded to calculate top profit products."
        return {
            "intent": "TOP_PROFIT_PRODUCTS",
            "reply": reply,
            "data": highest,
            "action_required": False,
        }

    # 6. Overall inventory stock valuation ("how much stock do I have", "inventory valuation")
    if any(k in q for k in [
        "how much stock do i have", "total stock", "overall stock", "kitna stock hai total", "inventory valuation",
        "motham stock", "mottha stock", "aake stock", "kul stock"
    ]):
        val = calculate_inventory_valuation(db, store_id=store_id)
        reply = (
            f"You have {val['total_stock_units']} total units in stock across {val['total_items_count']} products, "
            f"valued at ₹{val['total_cost_valuation']} cost price and ₹{val['total_selling_valuation']} estimated retail selling value."
        )
        return {
            "intent": "CHECK_TOTAL_STOCK",
            "reply": reply,
            "data": val,
            "action_required": False,
        }

    # 7. Unpaid supplier bills ("show unpaid supplier bills", "supplier dues")
    if any(k in q for k in [
        "unpaid supplier", "supplier bills", "unpaid bills", "supplier dues", "kitna dena hai",
        "baaki", "paaki", "bakaya", "udhar"
    ]):
        unpaid_orders = (
            db.query(PurchaseOrder)
            .filter(
                PurchaseOrder.store_id == store_id,
                PurchaseOrder.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .all()
        )
        unpaid_sum = sum(po.total_amount - po.paid_amount for po in unpaid_orders)
        return {
            "intent": "UNPAID_SUPPLIER_BILLS",
            "reply": f"You have {len(unpaid_orders)} unpaid supplier invoices totaling ₹{unpaid_sum:.2f} outstanding.",
            "data": {"count": len(unpaid_orders), "total_outstanding": unpaid_sum},
            "action_required": False,
        }

    # 8. Create an expense ("record an electricity expense of 500", "create an expense of 500 rupees for electricity")
    expense_match_1 = re.search(r"(create|add|record)\s+(?:an?\s+)?([a-zA-Z\s]+?)\s+expense\s+(?:of|for)?\s*(?:rupees|rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)", q)
    expense_match_2 = re.search(r"(create|add|record)\s+(?:an?\s+)?expense\s+of\s*(?:rupees|rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(?:rupees|rs\.?|inr)?\s+(?:for|on)\s+([a-zA-Z\s]+)", q)
    expense_match_3 = re.search(r"(create|add|record)\s+(?:an?\s+)?(?:rupees|rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(?:rupees|rs\.?|inr)?\s+(?:for\s+)?([a-zA-Z\s]+?)\s+expense", q)

    amount = None
    category_raw = None
    if expense_match_3:
        amount = float(expense_match_3.group(2))
        category_raw = expense_match_3.group(3).strip().title()
    elif expense_match_2:
        amount = float(expense_match_2.group(2))
        category_raw = expense_match_2.group(3).strip().title()
    elif expense_match_1:
        category_raw = expense_match_1.group(2).strip().title()
        amount = float(expense_match_1.group(3))

    if amount is not None and category_raw:
        cat_map = {
            "Electricity": "Electricity",
            "Bijli": "Electricity",
            "Power": "Electricity",
            "Rent": "Rent",
            "Kiraya": "Rent",
            "Salary": "Employee salaries",
            "Salaries": "Employee salaries",
            "Staff": "Employee salaries",
            "Transport": "Transport",
            "Delivery": "Transport",
            "Packaging": "Packaging",
            "Maintenance": "Maintenance",
            "Internet": "Internet and phone",
            "Phone": "Internet and phone",
            "Wifi": "Internet and phone",
        }
        category = cat_map.get(category_raw, category_raw)

        if not confirmed:
            payload_data = {"amount": amount, "category": category, "description": f"{category} expense via voice"}
            return {
                "intent": "CREATE_EXPENSE",
                "reply": f"Please confirm: Do you want to record an operating expense of ₹{amount:.2f} for {category}?",
                "data": payload_data,
                "action_payload": payload_data,
                "action_required": True,
                "action_type": "CONFIRM_CREATE_EXPENSE",
            }
        else:
            now = datetime.utcnow()
            cash_acc = (
                db.query(BusinessAccount)
                .filter(BusinessAccount.store_id == store_id, BusinessAccount.account_type == "CASH")
                .first()
            )
            exp = Expense(
                store_id=store_id,
                category=category,
                amount=amount,
                expense_date=now,
                payment_method="Cash",
                description=f"{category} expense via voice",
                business_account_id=cash_acc.id if cash_acc else None,
                status="ACTIVE",
                created_at=now,
            )
            db.add(exp)
            db.flush()

            if cash_acc:
                record_account_transaction(
                    db=db,
                    account_id=cash_acc.id,
                    transaction_type="EXPENSE",
                    amount=amount,
                    reference_type="EXPENSE",
                    reference_id=str(exp.id),
                    description=f"{category} expense",
                    store_id=store_id,
                )

            db.commit()
            return {
                "intent": "CREATE_EXPENSE",
                "reply": f"Successfully recorded an expense of ₹{amount:.2f} for {category}.",
                "data": {"expense_id": exp.id, "amount": amount, "category": category},
                "action_required": False,
            }

    # 9. Low-stock products
    if any(k in q for k in [
        "low in stock", "low stock", "reorder", "out of stock", "kam stock",
        "कम स्टॉक", "कम स्टॉक वाले", "खत्म होने वाला",
        "తక్కువ స్టాక్", "తక్కువ స్టాక్ ఉన్నవి", "takkuva stock",
        "குறைந்த இருப்பு", "kuraivaana stock",
        "ಕಡಿಮೆ ಸಂಗ್ರಹ", "ಕಡಿಮೆ ದಾಸ್ತಾನು", "kadime stock",
        "കുറഞ്ഞ സ്റ്റോക്ക്", "kuranja stock"
    ]):
        low_items = (
            db.query(Product)
            .filter(Product.store_id == store_id, Product.stock_qty <= Product.reorder_level, Product.is_active == 1)
            .all()
        )
        if not low_items:
            return {
                "intent": "LOW_STOCK",
                "reply": "All active products have healthy stock levels above reorder points.",
                "data": {"count": 0, "products": []},
                "action_required": False,
            }
        prod_names = [f"{p.name} ({p.stock_qty} {p.unit} remaining, reorder at {p.reorder_level})" for p in low_items[:5]]
        text_list = ", ".join(prod_names)
        return {
            "intent": "LOW_STOCK",
            "reply": f"Found {len(low_items)} low-stock products: {text_list}.",
            "data": {
                "count": len(low_items),
                "products": [{"id": p.id, "name": p.name, "sku": p.sku, "stock_qty": p.stock_qty, "reorder_level": p.reorder_level} for p in low_items],
            },
            "action_required": False,
        }

    # 10. Recent Sales / Bills
    if any(k in q for k in [
        "recent sales", "recent bills", "last bills", "last transactions", "aakhri bills",
        "pichli bikri", "chivara ammakaalu", "chivara bills"
    ]):
        recent = db.query(Sale).filter(Sale.store_id == store_id).order_by(Sale.created_at.desc()).limit(5).all()
        if not recent:
            return {
                "intent": "RECENT_SALES",
                "reply": "No sales recorded in the system yet.",
                "data": [],
                "action_required": False,
            }
        summary_str = "; ".join([f"{s.bill_number}: ₹{s.total} ({s.payment_method})" for s in recent])
        return {
            "intent": "RECENT_SALES",
            "reply": f"Recent 5 bills: {summary_str}.",
            "data": [{"id": s.id, "bill_number": s.bill_number, "total": s.total, "created_at": s.created_at.isoformat()} for s in recent],
            "action_required": False,
        }

    # Product Aliases mapping regional names to canonical catalogue items
    PRODUCT_ALIASES = {
        # Telugu
        "టాటా సాల్ట్": "Tata Salt",
        "టాటా ఉప్పు": "Tata Salt",
        "సాల్ట్": "Tata Salt",
        "ఉప్పు": "Tata Salt",
        "ఆశీర్వాద్ ఆటా": "Aashirvaad Atta",
        "ఆటా": "Aashirvaad Atta",
        "గోధుమ పిండి": "Aashirvaad Atta",
        "పార్లే జి": "Parle-G",
        "బిస్కట్": "Parle-G",
        "షుగర్": "Sugar",
        "చక్కెర": "Sugar",
        "పంచదార": "Sugar",
        "బియ్యం": "Basmati Rice",
        "రైస్": "Basmati Rice",
        "టాటా టీ": "Tata Tea",
        "టీ": "Tata Tea",
        "చాయ్": "Tata Tea",
        "నూనె": "Fortune Oil",
        "ఆయిల్": "Fortune Oil",
        "సబ్బు": "Dettol Soap",
        # Hindi
        "टाटा साल्ट": "Tata Salt",
        "टाटा नमक": "Tata Salt",
        "साल्ट": "Tata Salt",
        "नमक": "Tata Salt",
        "आशीर्वाद आटा": "Aashirvaad Atta",
        "आटा": "Aashirvaad Atta",
        "पारले जी": "Parle-G",
        "बिस्कुट": "Parle-G",
        "चीनी": "Sugar",
        "शक्कर": "Sugar",
        "चावल": "Basmati Rice",
        "चाय": "Tata Tea",
        "चायपत्ती": "Tata Tea",
        "तेल": "Fortune Oil",
        "साबुन": "Dettol Soap",
        # Tamil
        "டாடா உப்பு": "Tata Salt",
        "உப்பு": "Tata Salt",
        "ஆசீர்வாத் ஆட்டா": "Aashirvaad Atta",
        "ஆட்டா": "Aashirvaad Atta",
        # Kannada
        "ಟಾಟಾ ಉಪ್ಪು": "Tata Salt",
        "ಉಪ್ಪು": "Tata Salt",
        # Malayalam
        "ടാറ്റാ ഉപ്പ്": "Tata Salt",
        "ഉപ്പ്": "Tata Salt",
    }

    # 11. Check Stock for specific product
    stock_triggers = [
        "stock of", "how much stock", "kitna stock", "stock for", "check stock",
        "stock entha", "stock evalavu", "stock eshtu", "stock ethra", "stock",
        "స్టాక్ ఎంత", "స్టాక్", "నిల్వ ఎంత", "నిల్వ",
        "का स्टॉक कितना है", "का स्टॉक", "स्टॉक कितना है", "स्टॉक कितना", "स्टॉक",
        "இருப்பு எவ்வளவு", "இருப்பு",
        "ದಾಸ್ತಾನು ಎಷ್ಟು", "ದಾಸ್ತಾನು", "ಸಂಗ್ರಹ",
        "സ്റ്റോക്ക് എത്ര", "സ്റ്റോക്ക്"
    ]
    if any(k in q for k in stock_triggers) and not any(k in q for k in ["low in stock", "low stock", "kam stock", "takkuva", "తక్కువ", "कम"]):
        target = re.sub(r"[?.,!:]", "", q).strip()
        for pat in [
            r"^(?:show|tell|check|what is the|what is|how much)\s+(?:stock of|stock for|stock)?\s*",
            r"(?:stock of|how much stock|kitna stock|stock for|check stock|stock entha|stock evalavu|stock eshtu|stock ethra)\s*",
            r"\s*(?:ka stock kitna hai|ka stock|stock kitna hai|stock kitna|stock|entha|evalavu|eshtu|ethra)\s*$",
            r"\s*(?:స్టాక్\s*ఎంత(?:\s*ఉంది)?|స్టాక్|నిల్వ\s*ఎంత|నిల్వ)\s*$",
            r"\s*(?:का\s*स्टॉक\s*कितना\s*है|का\s*स्टॉक|स्टॉक\s*कितना\s*है|स्टॉक\s*कितना|स्टॉक)\s*$",
            r"\s*(?:இருப்பு\s*எவ்வளவு|இருப்பு)\s*$",
            r"\s*(?:ದಾಸ್ತಾನು\s*ಎಷ್ಟು|ದಾಸ್ತಾನು|ಸಂಗ್ರಹ)\s*$",
            r"\s*(?:സ്റ്റോക്ക്\s*എത്ര|സ്റ്റോക്ക്)\s*$",
            r"[?.,!]",
        ]:
            target = re.sub(pat, "", target, flags=re.I).strip()

        lookup_name = PRODUCT_ALIASES.get(target, PRODUCT_ALIASES.get(target.lower(), target))
        if lookup_name:
            prod = (
                db.query(Product)
                .filter(
                    Product.store_id == store_id,
                    (Product.name.ilike(f"%{lookup_name}%")) | (Product.sku.ilike(f"%{lookup_name}%")) | (Product.name.ilike(f"%{target}%")),
                    Product.is_active == 1,
                )
                .first()
            )
            if prod:
                status_str = "Low stock!" if prod.stock_qty <= prod.reorder_level else "In stock."
                return {
                    "intent": "CHECK_STOCK",
                    "reply": f"Current stock for {prod.name} ({prod.sku}) is {prod.stock_qty} {prod.unit}. {status_str}",
                    "data": {"product_id": prod.id, "sku": prod.sku, "name": prod.name, "stock_qty": prod.stock_qty, "price": prod.price, "unit": prod.unit},
                    "action_required": False,
                }
            else:
                return {
                    "intent": "CHECK_STOCK",
                    "reply": f"Could not find any active product matching '{target}'.",
                    "data": None,
                    "action_required": False,
                }

    # 12. Check Price for specific product
    price_triggers = [
        "price of", "cost of", "how much is", "kitne ka hai", "check price", "price",
        "dhara entha", "dhara", "vilai enna", "bele eshtu", "vila ethra",
        "ధర ఎంత", "ధర", "రేటు", "कीमत कितनी है", "कीमत", "दाम ఎంత", "दाम", "रेट"
    ]
    if any(k in q for k in price_triggers) and not any(k in q for k in ["add", "create", "new product", "record"]):
        target = re.sub(r"[?.,!:]", "", q).strip()
        for pat in [
            r"^(?:show|tell|check|what is the|what is|how much is)\s+(?:price of|cost of|price for|price)?\s*",
            r"(?:price of|cost of|how much is|kitne ka hai|check price|dhara entha|vilai enna|bele eshtu|vila ethra)\s*",
            r"\s*(?:price|cost|rate|kitne ka hai|dhara entha|vilai enna|bele eshtu|vila ethra)\s*$",
            r"\s*(?:ధర\s*ఎంత|ధర|రేటు)\s*$",
            r"\s*(?:कीमत\s*कितनी\s*है|कीमत|दाम|रेट)\s*$",
            r"[?.,!]",
        ]:
            target = re.sub(pat, "", target, flags=re.I).strip()

        lookup_name = PRODUCT_ALIASES.get(target, PRODUCT_ALIASES.get(target.lower(), target))
        if lookup_name:
            prod = (
                db.query(Product)
                .filter(
                    Product.store_id == store_id,
                    (Product.name.ilike(f"%{lookup_name}%")) | (Product.sku.ilike(f"%{lookup_name}%")) | (Product.name.ilike(f"%{target}%")),
                    Product.is_active == 1,
                )
                .first()
            )
            if prod:
                return {
                    "intent": "CHECK_PRICE",
                    "reply": f"The selling price of {prod.name} ({prod.sku}) is ₹{prod.price} per {prod.unit}.",
                    "data": {"product_id": prod.id, "sku": prod.sku, "name": prod.name, "price": prod.price, "unit": prod.unit},
                    "action_required": False,
                }
            else:
                return {
                    "intent": "CHECK_PRICE",
                    "reply": f"Could not find any product matching '{target}'.",
                    "data": None,
                    "action_required": False,
                }

    # 13. Search products
    if any(k in q for k in [
        "search", "find", "dhoondo", "look up", "show product",
        "vethuku", "thedu", "huduku", "thirayuka"
    ]):
        term = re.sub(r".*(search|find|dhoondo|look up|show product|vethuku|thedu|huduku|thirayuka)\s*", "", q).strip(" ?.")
        if term:
            prods = (
                db.query(Product)
                .filter(
                    Product.store_id == store_id,
                    (Product.name.ilike(f"%{term}%")) | (Product.sku.ilike(f"%{term}%")) | (Product.category.ilike(f"%{term}%")),
                    Product.is_active == 1,
                )
                .limit(5)
                .all()
            )
            if prods:
                items_str = ", ".join([f"{p.name} (₹{p.price}, {p.stock_qty} {p.unit})" for p in prods])
                return {
                    "intent": "SEARCH_PRODUCT",
                    "reply": f"Found {len(prods)} products: {items_str}.",
                    "data": [{"id": p.id, "sku": p.sku, "name": p.name, "price": p.price, "stock_qty": p.stock_qty} for p in prods],
                    "action_required": False,
                }
            else:
                return {
                    "intent": "SEARCH_PRODUCT",
                    "reply": f"No products found matching '{term}'.",
                    "data": [],
                    "action_required": False,
                }

    # 14. Stock Adjustment ("add 20 packets of rice to stock", "add 10 packs of Product Alpha")
    adj_match = re.search(r"(add|deduct|remove)\s+(\d+(?:\.\d+)?)\s*(?:packets?|packs?|units?|kg|pieces?|box(?:es)?)?\s*(?:of\s+)?([a-zA-Z0-9\s]+?)\s*(?:to|from)?\s*(?:stock)?$", q)
    if adj_match:
        direction = adj_match.group(1).lower()
        qty = float(adj_match.group(2))
        change = qty if direction == "add" else -qty
        p_name = adj_match.group(3).strip()

        prod = (
            db.query(Product)
            .filter(
                Product.store_id == store_id,
                (Product.name.ilike(f"%{p_name}%")) | (Product.sku.ilike(f"%{p_name}%")),
                Product.is_active == 1,
            )
            .first()
        )
        if prod:
            new_bal = round(prod.stock_qty + change, 4)
            if new_bal < 0:
                return {
                    "intent": "ADJUST_STOCK",
                    "reply": f"Cannot deduct {qty} {prod.unit} from {prod.name}. Available stock is only {prod.stock_qty} {prod.unit}.",
                    "data": None,
                    "action_required": False,
                }

            if not confirmed:
                action_word = "add" if change > 0 else "deduct"
                return {
                    "intent": "ADJUST_STOCK",
                    "reply": f"Please confirm: Do you want to {action_word} {abs(change)} {prod.unit} for {prod.name} ({prod.sku})? Stock will change from {prod.stock_qty} to {new_bal}.",
                    "data": {"product_id": prod.id, "sku": prod.sku, "name": prod.name, "quantity_change": change, "new_balance": new_bal},
                    "action_payload": {"intent": "ADJUST_STOCK", "product_id": prod.id, "quantity_change": change, "direction": direction},
                    "action_required": True,
                    "action_type": "CONFIRM_STOCK_ADJUSTMENT",
                }
            else:
                prod.stock_qty = new_bal
                mov = StockMovement(
                    product_id=prod.id,
                    quantity_change=change,
                    balance_after=new_bal,
                    reason="VOICE_ADJUSTMENT",
                    notes=f"Stock {direction}ed via voice command",
                    created_at=datetime.utcnow(),
                )
                db.add(mov)
                db.commit()
                return {
                    "intent": "ADJUST_STOCK",
                    "reply": f"Successfully {direction}ed {abs(change)} {prod.unit} for {prod.name}. New balance: {new_bal} {prod.unit}.",
                    "data": {"product_id": prod.id, "new_stock": new_bal},
                    "action_required": False,
                }

    # 15. Voice Bill Creation ("create bill for 2 Tata Salt and 1 Parle-G for customer Suresh")
    bill_match = re.search(r"(?:create\s+bill|make\s+bill|bill|sell)\s+(?:for\s+)?(.+?)(?:\s+(?:for|to)\s+customer\s+([a-zA-Z\s]+)|\s+to\s+([a-zA-Z\s]+))?[.]?$", q, re.I)
    if bill_match and not any(k in q for k in ["how much", "unpaid", "recent", "failed", "import", "expense"]):
        items_raw = bill_match.group(1).strip()
        cust_name = bill_match.group(2) or bill_match.group(3) or "Walk-in Customer"
        cust_name = cust_name.strip().title()

        item_tokens = re.split(r"\s+and\s+|,\s*", items_raw)
        staged_items = []
        unresolved = []
        insufficient = []
        total_calc = 0.0

        for tok in item_tokens:
            tok = tok.strip()
            if not tok:
                continue
            it_m = re.match(r"(\d+(?:\.\d+)?)\s*(?:packets?|packs?|units?|bags?|bottles?|jars?|pieces?|of)?\s*(.+)", tok, re.I)
            if not it_m:
                continue
            qty = float(it_m.group(1))
            search_name = it_m.group(2).replace("of", "").strip()

            prod = (
                db.query(Product)
                .filter(
                    Product.store_id == store_id,
                    (Product.name.ilike(f"%{search_name}%")) | (Product.sku.ilike(f"%{search_name}%")),
                    Product.is_active == 1,
                )
                .first()
            )
            if not prod:
                unresolved.append(search_name)
            elif prod.stock_qty < qty:
                insufficient.append(f"{prod.name} (need {qty}, have {prod.stock_qty})")
            else:
                line_tot = round(qty * prod.price, 2)
                total_calc += line_tot
                staged_items.append({
                    "product_id": prod.id,
                    "sku": prod.sku,
                    "name": prod.name,
                    "quantity": qty,
                    "price": prod.price,
                    "line_total": line_tot
                })

        if unresolved:
            return {
                "intent": "CREATE_BILL",
                "reply": f"Could not find active product(s) matching: {', '.join(unresolved)}.",
                "data": None,
                "action_required": False,
            }
        if insufficient:
            return {
                "intent": "CREATE_BILL",
                "reply": f"Insufficient stock for: {', '.join(insufficient)}.",
                "data": None,
                "action_required": False,
            }

        if staged_items:
            items_desc = ", ".join([f"{it['quantity']}x {it['name']} (₹{it['line_total']})" for it in staged_items])
            payload_data = {
                "intent": "CREATE_BILL",
                "customer_name": cust_name,
                "payment_method": "Cash",
                "items": staged_items,
                "total": total_calc
            }
            return {
                "intent": "CREATE_BILL",
                "reply": f"Please confirm: Do you want to create a bill for {cust_name} totaling ₹{total_calc:.2f} for {items_desc}?",
                "data": payload_data,
                "action_payload": payload_data,
                "action_required": True,
                "action_type": "CONFIRM_CREATE_BILL",
            }

    # 16. Voice Product Creation ("Add 10 packets of Aashirvaad Aata, each weighing 1 kg", "add new product Basmati Rice price 120 stock 50")
    prod_match_1 = re.search(r"(?:add\s+new\s+product|create\s+product|add\s+product)\s+([a-zA-Z0-9\s]+?)(?:\s+price\s*(\d+(?:\.\d+)?))?(?:\s+stock\s*(\d+(?:\.\d+)?))?[.]?$", q, re.I)
    prod_match_2 = re.search(r"(?:add|create)\s+(\d+(?:\.\d+)?)\s*(?:packets?|packs?|units?|bags?|bottles?|jars?|pieces?)?\s*(?:of\s+)?([a-zA-Z0-9\s]+?)(?:\s+(?:price|rate|at)\s*(\d+(?:\.\d+)?))?(?:,\s*each\s+weighing\s+(\d+(?:\.\d+)?\s*[a-zA-Z]+))?[.]?$", q, re.I)

    p_name = None
    p_price = 100.0
    p_stock = 0.0
    p_unit = "pack"

    if prod_match_1:
        p_name = prod_match_1.group(1).strip().title()
        if prod_match_1.group(2):
            p_price = float(prod_match_1.group(2))
        if prod_match_1.group(3):
            p_stock = float(prod_match_1.group(3))
    elif prod_match_2:
        p_stock = float(prod_match_2.group(1))
        p_raw = prod_match_2.group(2).strip().title()
        if prod_match_2.group(3):
            p_price = float(prod_match_2.group(3))
        weight = prod_match_2.group(4)
        p_name = f"{p_raw} {weight}" if weight else p_raw

    if p_name and not any(k in q for k in ["expense", "bill", "stock of", "price of"]):
        existing = db.query(Product).filter(Product.store_id == store_id, Product.name.ilike(f"%{p_name}%")).first()
        if not existing:
            payload_data = {
                "intent": "CREATE_PRODUCT",
                "name": p_name,
                "price": p_price,
                "cost_price": round(p_price * 0.8, 2),
                "stock_qty": p_stock,
                "unit": p_unit,
                "category": "General"
            }
            return {
                "intent": "CREATE_PRODUCT",
                "reply": f"Please confirm: Do you want to add new product '{p_name}' priced at ₹{p_price:.2f} with initial stock of {p_stock} {p_unit}?",
                "data": payload_data,
                "action_payload": payload_data,
                "action_required": True,
                "action_type": "CONFIRM_CREATE_PRODUCT",
            }

    # 17. Explain import errors
    if any(k in q for k in ["explain import", "import error", "why did bill fail", "failed import", "import problems"]):
        imp = (
            db.query(BillImport)
            .filter(
                BillImport.store_id == store_id,
                BillImport.status.in_(["REJECTED", "FAILED", "VALIDATED", "PENDING"]),
            )
            .order_by(BillImport.id.desc())
            .first()
        )
        if not imp:
            return {
                "intent": "EXPLAIN_IMPORT_ERRORS",
                "reply": "No recent bill imports found for your store.",
                "data": None,
                "action_required": False,
            }
        if imp.status == "REJECTED":
            reply = f"Import #{imp.id} ({imp.source_filename}) was rejected: {imp.error_message or 'Validation issues found in parsed items'}."
        elif imp.status == "FAILED":
            reply = f"Import #{imp.id} failed with system error: {imp.error_message or 'Unknown error'}."
        elif imp.status == "VALIDATED":
            reply = f"Import #{imp.id} ({imp.source_filename}) is valid with {imp.total_items} items totaling ₹{imp.total_amount}. It is awaiting confirmation."
        else:
            reply = f"Import #{imp.id} is in status '{imp.status}'."

        return {
            "intent": "EXPLAIN_IMPORT_ERRORS",
            "reply": reply,
            "data": {"import_id": imp.id, "status": imp.status, "error_message": imp.error_message},
            "action_required": False,
        }

    # 18. Rename store ("rename store to Supermart", "change store name to Bharat Kirana")
    rename_match = re.search(r"(?:rename\s+store\s+to|change\s+store\s+name\s+to|set\s+store\s+name\s+to|దుకాణం\s*పేరు\s*మార్చు|दुकान\s*का\s*नाम\s*बदलो)\s+([a-zA-Z0-9\s&'-]+?)[.]?$", q, re.I)
    if rename_match:
        new_name = rename_match.group(1).strip().title()
        payload_data = {"intent": "RENAME_STORE", "store_name": new_name}
        return {
            "intent": "RENAME_STORE",
            "reply": f"Please confirm: Do you want to rename your store to '{new_name}'?",
            "data": payload_data,
            "action_payload": payload_data,
            "action_required": True,
            "action_type": "CONFIRM_RENAME_STORE",
        }

    # Default fallback
    return {
        "intent": "UNKNOWN",
        "reply": f"I heard '{query}', but I didn't recognize that command. You can ask about today's profit, revenue, stock, cash balance, expenses, or top products.",
        "action_required": False,
    }


def process_voice_intent(
    query: str,
    db: Session,
    store_id: str = "DEFAULT",
    confirmed: bool = False,
    action_payload: Optional[dict] = None,
    language_code: str = "en-IN",
) -> Dict[str, Any]:
    """
    Parses natural language queries and executes safe operations with financial confirmation gates.
    Supports English and 5 regional languages (Telugu, Hindi, Tamil, Kannada, Malayalam).
    """
    raw_res = _internal_process_voice_intent(
        query=query,
        db=db,
        store_id=store_id,
        confirmed=confirmed,
        action_payload=action_payload,
    )
    return format_voice_result(raw_res, language_code)

