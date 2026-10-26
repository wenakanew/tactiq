import Link from "next/link";

export default function Brand() {
  return (
    <Link href="/" className="brand" aria-label="Tactiq home">
      <svg width="30" height="30" viewBox="0 0 32 32" fill="none" aria-hidden="true">
        <path d="M4 6h24M16 6v21M7 13h18" stroke="currentColor" strokeWidth="3" />
        <circle cx="26" cy="25" r="3" fill="currentColor" />
      </svg>
      <span>tactiq<span className="brand-period">.</span></span>
    </Link>
  );
}
