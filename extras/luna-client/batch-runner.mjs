// Orchestration only: official SDK owns auth, HTTP and stream handling.
export async function runBatch(items, {parallel, invoke, persist}) {
  if (![1, 2].includes(parallel) || items.length !== 2 ||
      new Set(items.map(item => item.id)).size !== 2 ||
      items.some(item => !/^[A-Za-z0-9_-]+$/u.test(item.id))) {
    throw new Error('Invalid fixed two-item batch');
  }
  const work = async item => {
    const result = await invoke(item);
    await persist(result);
    return result;
  };
  if (parallel === 2) {
    const settled = await Promise.allSettled(items.map(work));
    if (settled.some(item => item.status !== 'fulfilled')) {
      // Wait for the sibling too, including its independent persistence.
      throw new Error('Batch result persistence failed');
    }
    return settled.map(item => item.value);
  }
  const results = [];
  for (const item of items) {
    if (results.some(result => result.status !== 'completed')) {
      results.push({id:item.id, status:'not_started'});
      continue;
    }
    results.push(await work(item));
  }
  return results;
}
