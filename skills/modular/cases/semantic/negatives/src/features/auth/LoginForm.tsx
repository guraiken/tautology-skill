import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { signIn } from "@/lib/auth";

export function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const res = await signIn(email, password);
    if (res.ok) navigate("/dashboard");
    else setError(res.message);
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="rounded-md border px-3 py-2 text-sm" />
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="rounded-md border px-3 py-2 text-sm" />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" className="rounded-md bg-black px-4 py-2 text-sm text-white">Sign in</button>
    </form>
  );
}
