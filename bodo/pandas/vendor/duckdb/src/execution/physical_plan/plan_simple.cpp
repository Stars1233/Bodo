#include "duckdb/execution/operator/helper/physical_transaction.hpp"
#include "duckdb/execution/operator/helper/physical_vacuum.hpp"
#include "duckdb/execution/operator/schema/physical_alter.hpp"
#include "duckdb/execution/operator/schema/physical_attach.hpp"
#include "duckdb/execution/operator/schema/physical_create_schema.hpp"
#include "duckdb/execution/operator/schema/physical_create_view.hpp"
#include "duckdb/execution/operator/schema/physical_detach.hpp"
#include "duckdb/execution/operator/schema/physical_drop.hpp"
#include "duckdb/execution/physical_plan_generator.hpp"
#include "duckdb/planner/logical_operator.hpp"
#include "duckdb/planner/operator/logical_simple.hpp"

namespace duckdb {

PhysicalOperator &PhysicalPlanGenerator::CreatePlan(LogicalSimple &op) {
	switch (op.type) {
	case LogicalOperatorType::LOGICAL_ALTER:
		return Make<PhysicalAlter>(unique_ptr_cast<ParseInfo, AlterInfo>(std::move(op.info)), op.estimated_cardinality);
	case LogicalOperatorType::LOGICAL_DROP:
		return Make<PhysicalDrop>(unique_ptr_cast<ParseInfo, DropInfo>(std::move(op.info)), op.estimated_cardinality);
	case LogicalOperatorType::LOGICAL_TRANSACTION:
		return Make<PhysicalTransaction>(unique_ptr_cast<ParseInfo, TransactionInfo>(std::move(op.info)),
		                                 op.estimated_cardinality);
	case LogicalOperatorType::LOGICAL_ATTACH:
		return Make<PhysicalAttach>(unique_ptr_cast<ParseInfo, AttachInfo>(std::move(op.info)),
		                            op.estimated_cardinality);
	case LogicalOperatorType::LOGICAL_DETACH:
		return Make<PhysicalDetach>(unique_ptr_cast<ParseInfo, DetachInfo>(std::move(op.info)),
		                            op.estimated_cardinality);
	default:
		throw NotImplementedException("Unimplemented type for logical simple operator");
	}
}

} // namespace duckdb
