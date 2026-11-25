import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export function IssueToolbar() {
  return (
    <div className="flex gap-2">
      <Button>New issue</Button>
      <Button>Import</Button>
      <Button variant="default">Export</Button>
      <Button variant="ghost">More</Button>
    </div>
  );
}

export function IssueSidebar({ issue }) {
  return (
    <aside className="flex flex-col gap-3">
      <span>{issue.status}</span>
      <Separator />
      <span>{issue.owner}</span>
      <Separator />
      <span>{issue.team}</span>
      <Separator />
      <span>{issue.cycle}</span>
      <Separator />
      <span>{issue.estimate}</span>
    </aside>
  );
}

export function IssueRows({ issues }) {
  return (
    <ul>
      {issues.map((issue) => (
        <li key={issue.id} className="flex gap-2">
          <span>{issue.title}</span>
          <Badge variant="secondary">{issue.status}</Badge>
          <Badge variant="outline">{issue.priority}</Badge>
          <Badge variant="outline">{issue.team}</Badge>
        </li>
      ))}
    </ul>
  );
}

export function Overview() {
  return (
    <div className="grid grid-cols-3 gap-4">
      <StatCard label="Revenue" value="$48,200" />
      <StatCard label="Projects" value="24" />
      <StatCard label="Members" value="12" />
    </div>
  );
}
