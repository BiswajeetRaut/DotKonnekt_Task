import { FormEvent, useState } from "react";
import { Category } from "../api";

interface Props {
  categories: Category[];
  onCreate: (name: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

export function CategoryManager({ categories, onCreate, onDelete }: Props) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onCreate(name.trim());
      setName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add category");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await onDelete(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete category");
    }
  }

  return (
    <div className="card">
      <h2>Categories</h2>
      <ul className="category-list">
        {categories.map((c) => (
          <li key={c.id}>
            <span>{c.name}</span>
            <button type="button" onClick={() => handleDelete(c.id)}>
              Delete
            </button>
          </li>
        ))}
        {categories.length === 0 && <li>No categories yet — add one below.</li>}
      </ul>
      <form className="inline-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New category name"
          required
          maxLength={64}
        />
        <button type="submit" disabled={submitting || !name.trim()}>
          Add
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
