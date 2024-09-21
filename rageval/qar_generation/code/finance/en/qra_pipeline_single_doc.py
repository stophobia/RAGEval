import sys
import os
import json
import glob
import argparse
import logging
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

# Setup logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()
openai_api_key = os.getenv('OPENAI_API_KEY')

# Validate environment variables
if not openai_api_key:
    logging.error("OPENAI_API_KEY is not set in the environment.")
    sys.exit(1)

# Add root directory to system path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(root_dir)

from client import OpenAIClient as Client
from data_processing.postprocess import postprocess_en
from utils.utils import read_prompt, read_config_json, write_config_json

def update_config_summay_field(directory_path: str, base_file_name: str = "financial_report.json") -> None:
    """Update the summary field in the config."""
    
    base_file_path = os.path.join(directory_path, base_file_name)
    try:
        base_data = read_config_json(base_file_path)
        base_summary = base_data.get('Generated Summary', "")
        if not base_summary:
            print(f"'{base_file_name}' file does not contain a 'Generated Summary' field, or the field value is empty.")
            return
    except FileNotFoundError:
        print(f"FileNotFound: '{base_file_name}'")
        return
    except json.JSONDecodeError:
        print(f"File Format Error: '{base_file_name}' is not a valid JSON file.")
        return

    for filename in os.listdir(directory_path):
        file_path = os.path.join(directory_path, filename)
        
        if filename.endswith(".json") and filename != base_file_name:
            try:
                config = read_config_json(file_path)
                config['Generated Summary'] = base_summary
                write_config_json(file_path, config)
            except FileNotFoundError:
                print(f"FileNotFound: {filename}")
            except json.JSONDecodeError:
                print(f"File Format Error: '{filename}' is not a valid JSON file.")
                

def format_user_prompt(user_prompt: str, company_info: str, outline: str) -> str:
    """Format the user prompt with dynamic information."""
    return user_prompt.format(company_information=company_info, outline=outline)


def process_qra_document(model_name: str, file_path: str, prompts: List[Dict], input_dir: str, output_dir: str) -> None:
    """Process each document to generate QRA triples."""
    gpt_client = Client(
        openai_api_key=openai_api_key, 
        model_name=model_name
    )
    config = read_config_json(file_path)

    # Define company information based on the file type
    company_info = config['Company Information'] if file_path.endswith('financial_report.json') else config['Company Information']['Name']

    # Prepare prompt types
    prompt_types = {p['prompt_type']: p for p in prompts if p['prompt_type'] in ['Factual Question', 'Multi-hop Reasoning Question', 'Summarization Question', 'single document reference']}

    # Prepare QRA tasks
    qa_tasks = [
        {
            "system_prompt": prompt_types[key]['system_prompt'],
            "user_prompt": format_user_prompt(prompt_types[key]['user_prompt'], company_info, config['Generated Outline'])
        }
        for key in ['Factual Question', 'Multi-hop Reasoning Question', 'Summarization Question']
    ]

    # Generate responses
    responses = gpt_client.generate(qa_tasks)

    # Postprocess and update the config
    for i, key in enumerate(['qa_fact_based', 'qa_multi_hop', 'qa_summary']):
        responses[i] = postprocess_en(
            response=responses[i],
            system_prompt=qa_tasks[i]['system_prompt'],
            user_prompt=qa_tasks[i]['user_prompt'],
            model_name=model_name
        )
        config[key] = responses[i]

    # Additional processing for reference extraction
    doc_content = config['Generated Summary'] + config['Generated Article']
    qa_tasks = [
        {
            "system_prompt": prompt_types['single document reference']['system_prompt'],
            "user_prompt": prompt_types['single document reference']['user_prompt'].format(doc=doc_content, qa_pairs=config[key])
        }
        for key in ['qa_fact_based', 'qa_multi_hop', 'qa_summary']
    ]

    # Generate responses for reference extraction
    responses = gpt_client.generate(qa_tasks)

    # Postprocess and update the config
    for i, key in enumerate(['qa_fact_based', 'qa_multi_hop', 'qa_summary']):
        responses[i] = postprocess_en(
            response=responses[i],
            system_prompt=qa_tasks[i]['system_prompt'],
            user_prompt=qa_tasks[i]['user_prompt'],
            model_name=model_name
        )
        config[key] = responses[i]

    file = file_path.split('/')[-1]
    new_file_path = file_path.split(file)[0].replace(input_dir, output_dir)
    if not os.path.exists(new_file_path):
        os.makedirs(new_file_path)
    write_config_json(os.path.join(new_file_path, file), config)
    logging.info(f"Finished processing {os.path.join(new_file_path, file)}.")

def generate_qra(model_name: str, input_dir: str, output_dir: str, json_idx: int) -> None:
    """Generate QRA triples for each chapter in Finance."""
    prompts = read_prompt(
        file_path='prompts/finance_en.jsonl',
    )

    # Collect JSON files for processing
    json_files = []
    for root, dirs, files in os.walk(input_dir):
        for dir_name in dirs:
            if dir_name == str(json_idx):
                update_config_summay_field(
                    directory_path=os.path.join(root, dir_name),
                    base_file_name="financial_report.json"
                )
                json_files.extend(glob.glob(os.path.join(root, dir_name, '*.json')))

    # Process each file in parallel
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_qra_document, model_name, file_path, prompts, input_dir, output_dir) for file_path in json_files]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error processing file: {e}")


def main():
    parser = argparse.ArgumentParser(description='Generate QRA for a single document.')
    parser.add_argument('--model_name', type=str, required=True, help='Name of the OpenAI model to use')
    parser.add_argument('--input_dir', type=str, required=True, help='Directory containing input JSON files')
    parser.add_argument('--output_dir', type=str, required=True, help='Directory to save output JSON files')
    parser.add_argument('--json_idx', type=int, default=0, help='Index of the JSON files to process')

    args = parser.parse_args()
    
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)
    
    generate_qra(
        model_name=args.model_name, 
        input_dir=args.input_dir, 
        output_dir=args.output_dir,
        json_idx=args.json_idx
    )


if __name__ == "__main__":
    main()