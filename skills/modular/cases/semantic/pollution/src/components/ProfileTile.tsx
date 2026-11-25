export function ProfileTile({ name, role, skills }: { name: string; role: string; skills: string[] }) {
  return (
    <a href="#profile" className="block rounded-xl border bg-card/80 p-6">
      <h3 className="text-lg font-semibold">{name}</h3>
      <p className="mt-2 text-sm text-muted-foreground">{role}</p>
      <small className="text-xs text-muted-foreground">Joined 2021</small>
      <small className="text-xs text-muted-foreground">Remote</small>
      <ul className="mt-4 flex flex-wrap gap-2 text-sm">
        {skills.map((s) => (
          <li key={s} className="rounded-full bg-muted px-2 py-0.5">
            {s}
          </li>
        ))}
      </ul>
      <span className="mt-6 inline-flex rounded-md border px-4 py-2">Message</span>
    </a>
  );
}
