import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TodoFilters } from "./api/types";
import { FilterBar } from "./components/FilterBar";
import { TodoList } from "./components/TodoList";

const queryClient = new QueryClient();

function App() {
  const [filters, setFilters] = useState<TodoFilters>({});

  return (
    <QueryClientProvider client={queryClient}>
      <main>
        <h1>Todos</h1>
        <FilterBar filters={filters} onChange={setFilters} />
        <TodoList filters={filters} />
      </main>
    </QueryClientProvider>
  );
}

export default App;
