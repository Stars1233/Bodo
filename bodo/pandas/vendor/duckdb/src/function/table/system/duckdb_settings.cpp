#include "duckdb/function/table/system_functions.hpp"
#include "duckdb/main/config.hpp"
#include "duckdb/main/client_context.hpp"
#include "duckdb/common/enum_util.hpp"

namespace duckdb {

struct DuckDBSettingValue {
	string name;
	string value;
	string description;
	string input_type;
	string scope;
};

struct DuckDBSettingsData : public GlobalTableFunctionState {
	DuckDBSettingsData() : offset(0) {
	}

	vector<DuckDBSettingValue> settings;
	idx_t offset;
};

static unique_ptr<FunctionData> DuckDBSettingsBind(ClientContext &context, TableFunctionBindInput &input,
                                                   vector<LogicalType> &return_types, vector<string> &names) {
	names.emplace_back("name");
	return_types.emplace_back(LogicalType::VARCHAR);

	names.emplace_back("value");
	return_types.emplace_back(LogicalType::VARCHAR);

	names.emplace_back("description");
	return_types.emplace_back(LogicalType::VARCHAR);

	names.emplace_back("input_type");
	return_types.emplace_back(LogicalType::VARCHAR);

	names.emplace_back("scope");
	return_types.emplace_back(LogicalType::VARCHAR);

	return nullptr;
}

unique_ptr<GlobalTableFunctionState> DuckDBSettingsInit(ClientContext &context, TableFunctionInitInput &input) {
	auto result = make_uniq<DuckDBSettingsData>();

	auto &config = DBConfig::GetConfig(context);
	auto options_count = DBConfig::GetOptionCount();
	for (idx_t i = 0; i < options_count; i++) {
		auto option = DBConfig::GetOptionByIndex(i);
		D_ASSERT(option);
		DuckDBSettingValue value;
		auto scope = option->set_global ? SettingScope::GLOBAL : SettingScope::LOCAL;
		value.name = option->name;
		value.value = option->get_setting(context).ToString();
		value.description = option->description;
		value.input_type = option->parameter_type;
		value.scope = EnumUtil::ToString(scope);

		result->settings.push_back(std::move(value));
	}
	return std::move(result);
}

void DuckDBSettingsFunction(ClientContext &context, TableFunctionInput &data_p, DataChunk &output) {
	auto &data = data_p.global_state->Cast<DuckDBSettingsData>();
	if (data.offset >= data.settings.size()) {
		// finished returning values
		return;
	}
	// start returning values
	// either fill up the chunk or return all the remaining columns
	idx_t count = 0;
	while (data.offset < data.settings.size() && count < STANDARD_VECTOR_SIZE) {
		auto &entry = data.settings[data.offset++];

		// return values:
		// name, LogicalType::VARCHAR
		output.SetValue(0, count, Value(entry.name));
		// value, LogicalType::VARCHAR
		output.SetValue(1, count, Value(entry.value));
		// description, LogicalType::VARCHAR
		output.SetValue(2, count, Value(entry.description));
		// input_type, LogicalType::VARCHAR
		output.SetValue(3, count, Value(entry.input_type));
		// scope, LogicalType::VARCHAR
		output.SetValue(4, count, Value(entry.scope));
		count++;
	}
	output.SetCardinality(count);
}

void DuckDBSettingsFun::RegisterFunction(BuiltinFunctions &set) {
	set.AddFunction(
	    TableFunction("duckdb_settings", {}, DuckDBSettingsFunction, DuckDBSettingsBind, DuckDBSettingsInit));
}

} // namespace duckdb
