export default function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <span className="text-4xl mb-4 opacity-40">◈</span>
      <p className="text-text font-mono text-sm mb-2">No signals yet</p>
      <p className="text-muted text-xs max-w-xs">
        Run the scanner once to generate data. GitHub Actions will update this
        every time the workflow fires.
      </p>
      <a
        href="https://github.com/bsomar16/crypto-arb-scanner/actions"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-6 text-xs font-mono text-cyan border border-cyan/30 rounded px-3 py-1.5 hover:bg-cyan/5 transition-colors"
      >
        Trigger workflow →
      </a>
    </div>
  )
}
