"""
关联路径补全

召回到的候选表之间不一定直接相连，例如问“美妆个护的支付金额”时，可能只召回了
fact_order 和 dim_category，而两者之间还隔着 fact_order_item 和 dim_sku
本模块根据外键 ref 构成的表关系图，找出把候选表连成一体所需的中间表

关系图是有方向的：外键所在的表是“子表”，ref 指向的表是“父表”，子表到父表是多对一
同样长度的路径，业务含义可能完全不同：
- fact_order → fact_order_item → dim_sku → dim_category：沿订单明细找到商品，正确
- fact_order → dim_shop → dim_sku → dim_category：经由店铺展开到店铺的全部商品，
  会把每笔订单和店铺里所有商品都关联一遍，造成扇出
所以用带权最短路代替单纯的广度优先搜索：
- 子表 → 父表（多对一）：代价 1
- 事实表 → 引用它的子事实表（如订单 → 订单明细）：代价 1
- 维度表 → 引用它的表（从维度往下展开，容易扇出）：代价 3

做法是逐个把候选表接入已连通的集合，每次以已连通的全部表为起点求到下一个候选表的最短路径，
把路径上的中间表加入集合；起点按候选表顺序排列，代价相同时先加入的起点优先，结果稳定
"""

import heapq

UP_COST = 1
FACT_DOWN_COST = 1
DIM_DOWN_COST = 3


def find_bridge_tables(
    candidate_tables: list[str],
    edges: list[tuple[str, str]],
    fact_tables: set[str] | None = None,
) -> list[str]:
    """
    返回需要额外加入的中间表（不包含候选表本身），顺序稳定

    candidate_tables: 候选表，越靠前越优先作为连通起点（调用方应把事实表放在前面）
    edges: 外键关系 (子表, 父表)，即 (外键所在的表, ref 指向的表)
    fact_tables: 事实表集合，用来区分“从事实表往下”和“从维度表往下”两种展开
    """
    fact_tables = fact_tables or set()
    graph: dict[str, list[tuple[str, int]]] = {}
    for child, parent in edges:
        if child == parent:
            continue
        graph.setdefault(child, []).append((parent, UP_COST))
        down_cost = FACT_DOWN_COST if parent in fact_tables else DIM_DOWN_COST
        graph.setdefault(parent, []).append((child, down_cost))
    for neighbors in graph.values():
        neighbors.sort()

    candidates = list(dict.fromkeys(candidate_tables))
    if len(candidates) < 2:
        return []

    connected: list[str] = [candidates[0]]
    connected_set = {candidates[0]}
    bridges: list[str] = []

    for target in candidates[1:]:
        if target in connected_set:
            continue
        path = _cheapest_path(graph, connected, target)
        if path is None:
            # 图里没有任何路径相连，只能把它当作独立的表加入
            connected.append(target)
            connected_set.add(target)
            continue
        for table in path:
            if table not in connected_set:
                connected.append(table)
                connected_set.add(table)
                if table not in candidates:
                    bridges.append(table)
    return bridges


def _cheapest_path(
    graph: dict[str, list[tuple[str, int]]], sources: list[str], target: str
) -> list[str] | None:
    """多起点 Dijkstra，返回到达 target 的代价最小路径（包含起点和终点）"""
    # 堆元素 (累计代价, 入堆序号, 节点)，入堆序号保证代价相同时先入堆的优先
    heap: list[tuple[int, int, str]] = []
    parent: dict[str, str | None] = {}
    best: dict[str, int] = {}
    counter = 0
    for source in sources:
        heap.append((0, counter, source))
        best[source] = 0
        parent[source] = None
        counter += 1
    heapq.heapify(heap)
    visited: set[str] = set()

    while heap:
        cost, _, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == target:
            path = [node]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            return list(reversed(path))
        for neighbor, edge_cost in graph.get(node, []):
            new_cost = cost + edge_cost
            if neighbor not in best or new_cost < best[neighbor]:
                best[neighbor] = new_cost
                parent[neighbor] = node
                heapq.heappush(heap, (new_cost, counter, neighbor))
                counter += 1
    return None
