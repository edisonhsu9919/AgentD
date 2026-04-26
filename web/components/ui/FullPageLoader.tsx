export default function FullPageLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-text-secondary">
      <div className="surface-card flex items-center gap-3 px-5 py-4">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        <span className="font-caption text-sm">正在加载 AgentD 工作台</span>
      </div>
    </div>
  );
}
