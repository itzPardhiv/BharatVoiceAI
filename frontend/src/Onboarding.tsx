import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
  AudioLines, ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronRight,
  Languages, MapPin, Mic, Pause, Play, ShieldCheck, ShoppingBasket,
  Store, UserRound, Volume2, Waves, MessageSquareText
} from 'lucide-react'
import './onboarding.css'
import { useLanguage } from './i18n'
import { api } from './api'

export type StoreProfile = {
  ownerName: string
  mobile: string
  language: string
  languageLabel: string
  interaction: 'voice' | 'text' | 'both'
  storeName: string
  storeType: string
  city: string
  completedAt: string
}

type OnboardingProps = { onComplete: (profile: StoreProfile) => void }
type LanguageOption = { code: string; label: string; native: string; greeting: string; region: string }

const LANGUAGES: LanguageOption[] = [
  { code: 'te-IN', label: 'Telugu', native: 'తెలుగు', greeting: 'నమస్కారం! నేను భారత్‌వాయిస్. మీ దుకాణం నిర్వహణలో మీకు సహాయం చేస్తాను.', region: 'తెలుగు రాష్ట్రాలు' },
  { code: 'hi-IN', label: 'Hindi', native: 'हिन्दी', greeting: 'नमस्ते! मैं भारतवॉइस हूँ। मैं आपके स्टोर को संभालने में आपकी मदद करूँगा।', region: 'उत्तर भारत' },
  { code: 'en-IN', label: 'English', native: 'English', greeting: 'Hello! I am BharatVoice. I am here to help you run your store.', region: 'Across India' },
  { code: 'ta-IN', label: 'Tamil', native: 'தமிழ்', greeting: 'வணக்கம்! நான் பாரத்வாய்ஸ். உங்கள் கடையை நிர்வகிக்க உதவுகிறேன்.', region: 'தமிழ்நாடு' },
  { code: 'kn-IN', label: 'Kannada', native: 'ಕನ್ನಡ', greeting: 'ನಮಸ್ಕಾರ! ನಾನು ಭಾರತ್‌ವಾಯ್ಸ್. ನಿಮ್ಮ ಅಂಗಡಿಯನ್ನು ನಿರ್ವಹಿಸಲು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ.', region: 'ಕರ್ನಾಟಕ' },
  { code: 'ml-IN', label: 'Malayalam', native: 'മലയാളം', greeting: 'നമസ്കാരം! ഞാൻ ഭാരത്‌വോയ്സ്. നിങ്ങളുടെ കടയുടെ പ്രവർത്തനങ്ങൾ നിയന്ത്രിക്കാൻ സഹായിക്കാം.', region: 'കേരളം' },
]

const STORE_TYPES = ['Grocery / Kirana', 'Supermarket', 'Convenience store', 'Wholesale / Distributor', 'Other retail business']

export default function Onboarding({ onComplete }: OnboardingProps) {
  const { language, setLanguage, t } = useLanguage()
  const [step, setStep] = useState(0)
  const [ownerName, setOwnerName] = useState('')
  const [mobile, setMobile] = useState('')
  const [selectedCode, setSelectedCode] = useState(language === 'en-IN' ? 'te-IN' : language)
  const [interaction, setInteraction] = useState<StoreProfile['interaction']>('both')
  const [storeName, setStoreName] = useState('')
  const [storeType, setStoreType] = useState(STORE_TYPES[0])
  const [city, setCity] = useState('')
  const [playing, setPlaying] = useState(false)
  const [formError, setFormError] = useState('')
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const selectedLanguage = useMemo(() => LANGUAGES.find(item => item.code === selectedCode) ?? LANGUAGES[0], [selectedCode])
  const steps = ['Your details', 'Language', 'How you interact', 'Your store'].map(t)

  useEffect(() => {
    return () => {
      stopPreview()
    }
  }, [])

  function stopPreview() {
    if (audioRef.current) {
      try {
        audioRef.current.pause()
        audioRef.current.currentTime = 0
      } catch {}
      audioRef.current = null
    }
    setPlaying(false)
  }

  async function previewGreeting() {
    stopPreview()
    setFormError('')
    setPlaying(true)
    try {
      const res = await api.voiceTTS(selectedLanguage.greeting, selectedLanguage.code)
      if (res.audio_base64) {
        const audio = new Audio('data:audio/wav;base64,' + res.audio_base64)
        audioRef.current = audio
        audio.onended = () => {
          setPlaying(false)
          audioRef.current = null
        }
        audio.onerror = () => {
          setPlaying(false)
          audioRef.current = null
        }
        await audio.play()
      } else {
        setPlaying(false)
        setFormError(res.message || 'Gnani Timbre voice synthesis requires active GNANI_API_KEY.')
      }
    } catch (err: any) {
      setPlaying(false)
      setFormError(err?.message || 'Failed to synthesize voice preview via Gnani AI.')
    }
  }

  function nextFromSignup(event: FormEvent) {
    event.preventDefault()
    setFormError('')
    if (!ownerName.trim()) return setFormError('Please enter the store owner’s name.')
    if (!/^\d{10}$/.test(mobile.trim())) return setFormError('Enter a valid 10-digit Indian mobile number.')
    setStep(1)
  }

  function finishSetup(event: FormEvent) {
    event.preventDefault()
    setFormError('')
    if (!storeName.trim()) return setFormError('Please enter your store name.')
    if (!city.trim()) return setFormError('Please enter your city or town.')
    stopPreview()
    onComplete({
      ownerName: ownerName.trim(), mobile: mobile.trim(), language: selectedCode,
      languageLabel: selectedLanguage.label, interaction, storeName: storeName.trim(),
      storeType, city: city.trim(), completedAt: new Date().toISOString(),
    })
  }

  return <main className="onboarding-page">
    <div className="onboarding-topline">
      <a className="onboarding-brand" href="#welcome" aria-label="BharatVoice AI home">
        <span className="onboarding-brand-mark"><AudioLines size={22}/></span>
        <span><strong>BharatVoice <i>AI</i></strong><small>RETAIL, IN YOUR LANGUAGE</small></span>
      </a>
      <div className="onboarding-secure"><ShieldCheck size={15}/> Your setup stays on this device for now</div>
    </div>

    <div className="onboarding-layout">
      <section className="onboarding-story">
        <div className="story-kicker"><span/> A simpler way to run your store</div>
        <h1>Your store has a lot to do.<br/><em>Let your voice lead.</em></h1>
        <p className="story-copy">BharatVoice helps you manage everyday retail work through a language you are comfortable speaking.</p>
        <div className="story-visual" aria-label="Illustration of a retail shop and voice assistant">
          <div className="visual-orbit orbit-one"/><div className="visual-orbit orbit-two"/>
          <div className="visual-shop"><div className="shop-awning"><span/><span/><span/><span/><span/></div><div className="shop-sign">మీ దుకాణం <small>YOUR STORE</small></div><div className="shop-shelves"><i/><i/><i/><i/><i/><i/><i/><i/></div><div className="shop-counter"/></div>
          <div className="visual-wave"><AudioLines size={27}/><span/><span/><span/><span/><span/></div>
          <div className="visual-note note-language"><Languages size={15}/> Your language</div>
          <div className="visual-note note-retail"><ShoppingBasket size={15}/> Your business</div>
        </div>
        <div className="story-foot"><span>01</span><div/><span>Made for Indian retail businesses</span></div>
      </section>

      <section className="onboarding-card" aria-live="polite">
        <div className="card-topline"><span>{t("LET'S GET YOU STARTED")}</span><span>STEP {step + 1} OF 4</span></div>
        <div className="step-track">{steps.map((label, index) => <div key={label} className={`step-segment ${index <= step ? 'done' : ''}`}/>)}</div>

        {step === 0 && <form onSubmit={nextFromSignup} className="onboarding-form">
          <div className="step-icon"><UserRound size={21}/></div>
          <div className="step-eyebrow">{t("FIRST, A LITTLE INTRODUCTION")}</div>
          <h2>{t("Who are we helping today?")}</h2>
          <p className="step-description">Tell us who runs the store. We'll make the experience feel familiar from the start.</p>
          <label className="field-label" htmlFor="ownerName">{t("Your name")}</label>
          <div className="field-wrap"><UserRound size={17}/><input id="ownerName" value={ownerName} onChange={e => setOwnerName(e.target.value)} placeholder={t("e.g. Anil Kumar")} autoComplete="name"/></div>
          <label className="field-label" htmlFor="mobile">{t("Mobile number")}</label>
          <div className="field-wrap phone-field"><span className="country-code">+91</span><span className="field-divider"/><input id="mobile" inputMode="numeric" maxLength={10} value={mobile} onChange={e => setMobile(e.target.value.replace(/\D/g, '').slice(0, 10))} placeholder={t("10-digit mobile number")} autoComplete="tel-national"/></div>
          <p className="field-hint">We'll use this for your account when secure sign-in is connected.</p>
          {formError && <div className="form-error">{formError}</div>}
          <button className="onboarding-primary" type="submit">Continue <ArrowRight size={17}/></button>
          <p className="privacy-note"><ShieldCheck size={14}/> No OTP or account is created in this frontend preview.</p>
        </form>}

        {step === 1 && <div className="onboarding-form">
          <div className="step-icon"><Languages size={21}/></div>
          <div className="step-eyebrow">{t("YOUR LANGUAGE, YOUR COMFORT")}</div>
          <h2>{t("Which language feels like home?")}</h2>
          <p className="step-description">BharatVoice will use this as your preferred language for conversations and the experience.</p>
          <div className="language-grid">{LANGUAGES.map(item => <button type="button" key={item.code} className={`language-option ${selectedCode === item.code ? 'selected' : ''}`} onClick={() => { setSelectedCode(item.code); setLanguage(item.code); setFormError(''); stopPreview() }}>
            <span className="language-native">{item.native}</span><span className="language-english">{item.label}</span><span className="language-region">{item.region}</span>{selectedCode === item.code && <span className="language-check"><Check size={13}/></span>}
          </button>)}</div>
          <div className="greeting-preview"><div className="greeting-icon"><Volume2 size={17}/></div><div className="greeting-copy"><strong>{t("Hear how BharatVoice greets you")}</strong><span>{selectedLanguage.greeting}</span></div><button className="preview-button" type="button" onClick={playing ? stopPreview : previewGreeting} aria-label={playing ? 'Stop greeting preview' : 'Play greeting preview'}>{playing ? <Pause size={16}/> : <Play size={16}/>}</button></div>
          <p className="field-hint">Audio preview uses your browser's available voice, not Gnani Timbre.</p>
          {formError && <div className="form-error">{formError}</div>}
          <div className="onboarding-actions"><button className="onboarding-back" type="button" onClick={() => setStep(0)}><ArrowLeft size={16}/> Back</button><button className="onboarding-primary" type="button" onClick={() => setStep(2)}>This is my language <ArrowRight size={17}/></button></div>
        </div>}

        {step === 2 && <div className="onboarding-form">
          <div className="step-icon"><Waves size={21}/></div>
          <div className="step-eyebrow">{t("HOW WOULD YOU LIKE TO USE IT?")}</div>
          <h2>{t("Speak, type, or both?")}</h2>
          <p className="step-description">Choose how you want to give instructions. You can change this later.</p>
          <div className="interaction-options">
            <button type="button" className={`interaction-option ${interaction === 'voice' ? 'selected' : ''}`} onClick={() => setInteraction('voice')}><span className="interaction-icon"><Mic size={22}/></span><span className="interaction-text"><strong>{t("Mostly by voice")}</strong><small>Talk naturally to manage your store.</small></span><span className="choice-radio"/></button>
            <button type="button" className={`interaction-option ${interaction === 'text' ? 'selected' : ''}`} onClick={() => setInteraction('text')}><span className="interaction-icon"><MessageSquareText size={22}/></span><span className="interaction-text"><strong>{t("Mostly by typing")}</strong><small>Type commands and read responses.</small></span><span className="choice-radio"/></button>
            <button type="button" className={`interaction-option ${interaction === 'both' ? 'selected' : ''}`} onClick={() => setInteraction('both')}><span className="interaction-icon"><AudioLines size={22}/></span><span className="interaction-text"><strong>{t("A mix of both")}</strong><small>Speak when it's convenient, type when needed.</small></span><span className="choice-radio"/></button>
          </div>
          <div className="reassurance-strip"><CheckCircle2 size={17}/><span>You can always use touch and text controls, even if you choose voice-first.</span></div>
          <div className="onboarding-actions"><button className="onboarding-back" type="button" onClick={() => setStep(1)}><ArrowLeft size={16}/> Back</button><button className="onboarding-primary" type="button" onClick={() => setStep(3)}>Continue <ArrowRight size={17}/></button></div>
        </div>}

        {step === 3 && <form onSubmit={finishSetup} className="onboarding-form">
          <div className="step-icon"><Store size={21}/></div>
          <div className="step-eyebrow">{t("LET'S SET UP YOUR BUSINESS")}</div>
          <h2>{t("What should we call your store?")}</h2>
          <p className="step-description">A few details help BharatVoice understand the kind of retail work you do.</p>
          <label className="field-label" htmlFor="storeName">{t("Store name")}</label>
          <div className="field-wrap"><Store size={17}/><input id="storeName" value={storeName} onChange={e => setStoreName(e.target.value)} placeholder={t("e.g. Sri Lakshmi Stores")}/></div>
          <label className="field-label" htmlFor="storeType">{t("Type of business")}</label>
          <div className="select-wrap"><ShoppingBasket size={17}/><select id="storeType" value={storeType} onChange={e => setStoreType(e.target.value)}>{STORE_TYPES.map(type => <option key={type}>{type}</option>)}</select><ChevronRight size={16}/></div>
          <label className="field-label" htmlFor="city">{t("City or town")}</label>
          <div className="field-wrap"><MapPin size={17}/><input id="city" value={city} onChange={e => setCity(e.target.value)} placeholder={t("e.g. Hyderabad")} autoComplete="address-level2"/></div>
          {formError && <div className="form-error">{formError}</div>}
          <div className="onboarding-actions"><button className="onboarding-back" type="button" onClick={() => setStep(2)}><ArrowLeft size={16}/> Back</button><button className="onboarding-primary" type="submit">Set up my store <ArrowRight size={17}/></button></div>
          <p className="privacy-note"><ShieldCheck size={14}/> Your details are saved locally in this preview until backend signup is connected.</p>
        </form>}
      </section>
    </div>
    <footer className="onboarding-footer"><span>BHARATVOICE AI</span><span>Built around the way Indian stores work.</span><span>Language: {selectedLanguage.label}</span></footer>
  </main>
}
