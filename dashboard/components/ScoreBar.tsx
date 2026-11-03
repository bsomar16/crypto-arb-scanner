interface Props { score: number }

export default function ScoreBar({ score }: Props) {
  const color =
    score >= 75 ? '#00e5ff' :
    score >= 60 ? '#22c55e' :
    score >= 50 ? '#f59e0b' : '#4b6480'

  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full bg-border overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${score}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono text-xs" style={{ color }}>{score}</span>
    </div>
  )
}
