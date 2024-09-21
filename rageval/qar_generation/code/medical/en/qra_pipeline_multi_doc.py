import sys
import os
import argparse
import logging
import random
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

def process_qra_documents(model_name: str,
    input_dir: str,
    output_dir: str,
    json_idx: int,
    topic_1: str,
    topic_2: str,
    select_disease_1: str,
    select_disease_2: str,
    prompts: List[Dict]
) -> None:
    """Processes QRA documents using GPT client."""
    
    gpt_client = Client(
        openai_api_key=openai_api_key, 
        model_name=model_name
    )
    
    topic_path_1 = os.path.join(input_dir, topic_1, str(json_idx), select_disease_1)
    topic_path_2 = os.path.join(input_dir, topic_2, str(json_idx), select_disease_2)
    
    config_1_raw = read_config_json(topic_path_1)
    config_2_raw = read_config_json(topic_path_2)
    
    config_1 = {
        "Hospitalization Record": config_1_raw['Hospitalization Record'],
        "DiseaseType": config_1_raw['DiseaseType'],
    }
    config_2 = {
        "Hospitalization Record": config_2_raw['Hospitalization Record'],
        "DiseaseType": config_2_raw['DiseaseType'],
    }
    
    # Prepare prompt types
    prompt_types = {p['prompt_type']: p for p in prompts if p['prompt_type'] in ['Multi-document Information Integration Question', 'Multi-document Comparison Question', 'multi document reference']}
    
    # Prepare QRA tasks
    qa_tasks = [
        {
            "system_prompt": prompt_types[key]['system_prompt'],
            "user_prompt": prompt_types[key]['user_prompt'].format(
                config_1=config_1,
                config_2=config_2
            )
        }
        for key in ['Multi-document Information Integration Question', 'Multi-document Comparison Question']
    ]
    
    # Generate responses
    responses = gpt_client.generate(qa_tasks)
    
    # Postprocess and update the config
    for i, key in enumerate(['qa_multi_document_information_integration', 'qa_multi_document_compare']):
        responses[i] = postprocess_en(
            response=responses[i],
            system_prompt=qa_tasks[i]['system_prompt'],
            user_prompt=qa_tasks[i]['user_prompt'],
            model_name=model_name
        )
        config_1[key] = responses[i]
    
    # Additional processing for reference extraction
    doc_content_1 = config_1_raw['Generated Article']
    doc_content_2 = config_2_raw['Generated Article']
    qa_tasks = [
        {
            "system_prompt": prompt_types['multi document reference']['system_prompt'],
            "user_prompt": prompt_types['multi document reference']['user_prompt'].format(
                doc_1=doc_content_1,
                doc_2=doc_content_2,
                qa_pairs=config_1[key]
            )
        }
        for key in ['qa_multi_document_information_integration', 'qa_multi_document_compare']
    ]
    
    # Generate responses
    responses = gpt_client.generate(qa_tasks)
    
    result_json_obj = {
        'qa_multi_document_information_integration': [],
        'qa_multi_document_compare': []
    }
    
    # Postprocess and create multi-doc json files
    for i, key in enumerate(['qa_multi_document_information_integration', 'qa_multi_document_compare']):
        responses[i] = postprocess_en(
            response=responses[i],
            system_prompt=qa_tasks[i]['system_prompt'],
            user_prompt=qa_tasks[i]['user_prompt'],
            model_name=model_name
        )
        result_json_obj[key] = responses[i]
    
    disease_name_1 = select_disease_1.split('.json')[0]
    disease_name_2 = select_disease_2.split('.json')[0]
    new_file_name = f'{disease_name_1}_{disease_name_2}.json'
    new_file_path = os.path.join(output_dir, new_file_name)
    write_config_json(new_file_path, result_json_obj)
    logging.info(f"Finished processing {new_file_path}.")

def generate_qra(model_name: str,
    input_dir: str,
    output_dir: str,
    json_idx: int,
    number: int
) -> None:
    """Generate QRA triples for random two documents in the specific json_idx in Finance."""
    prompts = read_prompt(
        file_path='prompts/medical_en.jsonl',
    )
    
    topic_list = [
        "Benign, In Situ, and Uncertain Neoplasms", "Circulatory System Diseases", "Conditions Originating in the Perinatal Period", "Congenital Anomalies", "Digestive System Diseases", "Diseases of the Blood and Blood-Forming Organs", "Diseases of the Ear and Mastoid Process", "Diseases of the Eye and Adnexa", "Diseases of the Genitourinary System", "Diseases of the Musculoskeletal System and Connective Tissue", "Diseases of the Nervous System", "Diseases of the Skin and Subcutaneous Tissue", "Endocrine, Nutritional, and Metabolic Diseases and Immune Disorders", "Infectious Diseases", "Malignant Tumors", "Mental Disorders", "Parasitic Diseases", "Pregnancy, Childbirth, and the Puerperium Complications", "Respiratory System Diseases"
    ]
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for _ in range(number):
            select_list_topic = random.sample(topic_list, 2)
            topic_1, topic_2 = select_list_topic
            disease_list_1 = os.listdir(os.path.join(input_dir, topic_1, str(json_idx)))
            disease_list_2 = os.listdir(os.path.join(input_dir, topic_2, str(json_idx)))
            select_disease_1 = random.sample(disease_list_1, 1)[0]
            select_disease_2 = random.sample(disease_list_2, 1)[0]
            
            futures.append(executor.submit(
                process_qra_documents, model_name, input_dir, output_dir, json_idx, topic_1, topic_2, select_disease_1, select_disease_2, prompts
            ))
        
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error processing future: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model_name', type=str, default='gpt-4o')
    parser.add_argument('--input_dir', type=str, default=None)
    parser.add_argument('--output_dir', type=str, default=None)
    parser.add_argument('--json_idx', type=int, default=0)
    parser.add_argument('--number', type=int, default=1)
    
    args = parser.parse_args()
    
    output_dir = os.path.join(args.output_dir, str(args.json_idx))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    generate_qra(
        model_name=args.model_name, 
        input_dir=args.input_dir, 
        output_dir=args.output_dir, 
        json_idx=args.json_idx, 
        number=args.number
    )
    
if __name__ == "__main__":
    main()
        
    
    
    