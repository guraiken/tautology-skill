import { useState } from "react";
import { subscribe } from "@/lib/newsletter";

export function NewsletterSignup() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "done" | "error">("idle");
  const [topic, setTopic] = useState("product");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await subscribe(email, topic);
    setStatus(ok ? "done" : "error");
  }

  if (status === "done") return <p className="text-sm text-green-700">Thanks for subscribing!</p>;

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="rounded-md border px-3 py-2 text-sm" />
      <select value={topic} onChange={(e) => setTopic(e.target.value)} className="rounded-md border px-3 py-2 text-sm">
        <option value="product">Product updates</option>
        <option value="blog">Blog</option>
      </select>
      {status === "error" && <p className="text-sm text-red-600">Something went wrong</p>}
      <button type="submit" className="rounded-md bg-black px-4 py-2 text-sm text-white">Subscribe</button>
    </form>
  );
}
