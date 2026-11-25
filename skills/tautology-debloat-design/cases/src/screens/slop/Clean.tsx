import { cn } from "@/lib/utils";

const panel = cn("rounded-lg border bg-card p-4", "shadow-sm");

export function ChatThread({ typing }) {
  return (
    <div className={panel}>
      {typing && <span className="size-2 rounded-full bg-muted-foreground animate-pulse" aria-live="polite" />}
    </div>
  );
}

export function PostRows({ posts }) {
  return (
    <ul>
      {posts.map((post) => (
        <li key={post.id} className="flex gap-2">
          <a className="text-3xl font-bold">{post.title}</a>
          <span className="text-xs text-muted-foreground">{post.date}</span>
          <Badge variant="secondary">{post.tag}</Badge>
        </li>
      ))}
    </ul>
  );
}

export function Plans() {
  return (
    <div className="flex gap-2">
      <div className="rounded-lg border p-4"><span className="text-sm">Pro</span><span className="text-3xl">$12</span><span className="text-xs">/mo</span></div>
      <Button variant="outline">Cancel</Button>
      <Button>Save</Button>
    </div>
  );
}
