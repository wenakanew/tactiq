import Link from "next/link";
import { ArrowDown, ArrowRight, AudioLines, Crosshair, Layers3, MoveUpRight, Play, ScanLine, ShieldCheck } from "lucide-react";
import Brand from "../components/Brand";
import PitchIllustration from "../components/landing/PitchIllustration";

export default function HomePage() {
  return (
    <div className="landing">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header page-width">
        <Brand />
        <nav className="site-nav" aria-label="Main navigation">
          <a href="#experience">The experience</a>
          <a href="#how-it-works">How it works</a>
        </nav>
        <Link href="/workspace" prefetch={false} className="site-workspace-link">Open workspace <MoveUpRight size={15} aria-hidden="true" /></Link>
      </header>

      <main id="main-content" tabIndex={-1}>
      <section className="landing-hero page-width">
        <div className="hero-copy">
          <p className="eyebrow"><span className="signal-dot" /> A different perspective on the game</p>
          <h1>Watch the game.<br />Understand the <em>why.</em></h1>
          <p className="hero-description">The run that opens a defence. The pressure that changes a match. Find the moments that matter, and the evidence behind them.</p>
          <div className="hero-actions">
            <Link href="/workspace?start=demo" prefetch={false} className="btn btn--primary">Explore a match <ArrowRight size={17} aria-hidden="true" /></Link>
            <Link href="/workspace?start=video" prefetch={false} className="btn btn--outline">Analyze your video</Link>
          </div>
          <div className="hero-footnote"><span className="fine-rule" /><span>Football intelligence.<br /><strong>More perspective. Less guesswork.</strong></span></div>
        </div>
        <div className="hero-visual">
          <div className="preview-topline"><span className="mini-wordmark">TACTIQ / MATCH VIEW</span><span className="preview-label">Illustrative preview</span></div>
          <div className="pitch-perspective"><PitchIllustration /></div>
          <div className="floating-moment">
            <span className="moment-number">01</span>
            <div><span className="eyebrow">Look beyond the ball</span><h2>A small shift.<br />A new opening.</h2></div>
            <MoveUpRight size={22} aria-hidden="true" />
          </div>
          <div className="preview-caption"><span><span className="signal-dot" /> Follow the pattern</span><span>Moment <ArrowRight size={12} /> Evidence <ArrowRight size={12} /> Insight</span></div>
        </div>
      </section>

      <div className="landing-strip page-width">
        <span>See it. Understand it. Make it yours.</span>
        <div><ScanLine size={16} aria-hidden="true" /> Match tracking</div>
        <div><Layers3 size={16} aria-hidden="true" /> Tactical replay</div>
        <div><AudioLines size={16} aria-hidden="true" /> Two perspectives</div>
        <a href="#experience" aria-label="Discover the Tactiq experience"><ArrowDown size={17} aria-hidden="true" /></a>
      </div>

      <section className="experience-section page-width" id="experience">
        <div className="section-intro"><p className="eyebrow">Built around the beautiful game</p><h2>Not just what happened.<br /><span>What made it happen.</span></h2></div>
        <div className="experience-grid">
          <article className="experience-feature"><div className="feature-symbol"><Crosshair size={23} /></div><span className="feature-index">01 / SEE THE PATTERN</span><h3>The game between<br />the moments.</h3><p>Explore pressure, transitions, player influence, and team shape. Move from a match moment to its supporting signals without losing the picture.</p><Link href="/workspace">Find your perspective <ArrowRight size={15} /></Link></article>
          <article className="experience-feature"><div className="feature-symbol"><Play size={23} /></div><span className="feature-index">02 / GO BACK TO THE WHY</span><h3>Every moment.<br />A clearer view.</h3><p>Replay the action alongside available tracking. Seek into the timeline, inspect a player, and open the evidence right where you need it.</p><Link href="/workspace?start=video">Explore analysis <ArrowRight size={15} /></Link></article>
          <article className="experience-feature"><div className="feature-symbol"><AudioLines size={23} /></div><span className="feature-index">03 / HEAR ANOTHER ANGLE</span><h3>One game.<br />Your kind of story.</h3><p>Switch between fan, analyst, and player perspectives. Read or listen to supported explanations, with content in seven languages.</p><Link href="/workspace?start=demo">Meet your match <ArrowRight size={15} /></Link></article>
        </div>
      </section>

      <section className="workflow-section" id="how-it-works">
        <div className="page-width workflow-inner">
          <div><p className="eyebrow">Less setup. More understanding.</p><h2>From the first whistle<br />to the <em>full picture.</em></h2><p className="section-description">Start with a simulated scenario or bring a video you have the rights to analyze. Tactiq keeps the match, its moments, and their explanations connected.</p><Link href="/workspace?start=demo" className="text-link">Step into the workspace <ArrowRight size={17} /></Link></div>
          <ol className="workflow-steps">
            <li><span>01</span><div><h3>Choose your match</h3><p>Explore a labelled demo, upload a video, or revisit a saved analysis.</p></div></li>
            <li><span>02</span><div><h3>Follow what matters</h3><p>Move through the match with replay, selected moments, and inline evidence.</p></div></li>
            <li><span>03</span><div><h3>Make sense of the story</h3><p>Choose a perspective, listen to the explanation, and revisit the recap.</p></div></li>
          </ol>
        </div>
      </section>

      <section className="closing-section page-width">
        <div className="closing-mark"><ShieldCheck size={20} aria-hidden="true" /><span>Evidence before explanation.<br />Source and limitations, always in view.</span></div>
        <h2>There is more to<br /><em>every match.</em></h2>
        <Link href="/workspace" prefetch={false} className="btn btn--primary">Find your perspective <ArrowRight size={17} /></Link>
      </section>
      </main>
      <footer className="site-footer page-width"><Brand /><p>Independent football intelligence.<br />Built for the Inside the Game hackathon.</p><span>See the game differently.</span><Link href="/workspace">Enter workspace <MoveUpRight size={14} /></Link></footer>
    </div>
  );
}
